"""Pure parameter parsing and batch planning; no OMERO or NumPy imports."""
import hashlib
import json
import math
import re
from decimal import Decimal


class ValidationError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def radius_token(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValidationError("Radius must be finite and nonnegative")
    if number == 0:
        return "0"
    token = format(Decimal(repr(number)), "f")
    return token.rstrip("0").rstrip(".") if "." in token else token


def output_name(name, radii, max_length):
    base = name
    for suffix in (".ome.tiff", ".ome.tif", ".tiff", ".tif"):
        if name.lower().endswith(suffix):
            base = name[:-len(suffix)]
            break
    else:
        dot = name.rfind(".")
        if 0 < dot < len(name) - 1:
            base = name[:dot]
    if not base:
        raise ValidationError("Source name has an empty basename")
    result = base + "_bgs-" + "-".join(radius_token(r) for r in radii) + ".tif"
    if len(result) > max_length:
        raise ValidationError("Output name exceeds configured OMERO name limit")
    return result


def positive_id(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(label + " must be a positive integer")
    return value


def parse_inputs(values, limits):
    p = dict(values)
    enums = {
        "Data_Type": ("Image", "Dataset"), "Run_Mode": ("Preview", "Process"),
        "Name_Match_Mode": ("Contains", "Exact", "Wildcard"),
        "Channel_Match_Mode": ("Name", "Index"),
        "Output_Mode": ("Existing_Dataset", "New_Dataset"),
    }
    defaults = {"Run_Mode": "Preview", "Data_Type": "Image", "Name_Match_Mode": "Contains",
                "Channel_Match_Mode": "Name", "Output_Mode": "Existing_Dataset"}
    for key, allowed in enums.items():
        p[key] = p.get(key, defaults[key])
        if p[key] not in allowed:
            raise ValidationError("Invalid " + key)
    ids = p.get("IDs", [])
    if not isinstance(ids, (list, tuple)) or not ids:
        raise ValidationError("Supply at least one image ID or exactly one dataset ID")
    if p["Data_Type"] == "Dataset" and len(ids) != 1:
        raise ValidationError("Dataset input requires exactly one dataset ID")
    p["IDs"] = sorted({positive_id(i, "ID") for i in ids})
    p["Name_Filter"] = str(p.get("Name_Filter") or "")
    if len(p["Name_Filter"]) > 1024:
        raise ValidationError("Name filter must contain at most 1024 characters")
    p["Case_Sensitive"] = p.get("Case_Sensitive", True)
    if not isinstance(p["Case_Sensitive"], bool):
        raise ValidationError("Case_Sensitive must be a boolean")
    rules = {}
    for i in range(1, 9):
        key = str(p.get(f"Rule_{i}_Channel") or "").strip()
        radius = p.get(f"Rule_{i}_Radius")
        if not key and radius is None:
            continue
        if not key or radius is None:
            raise ValidationError(f"Rule {i}: supply both channel and radius (0 is allowed)")
        if p["Channel_Match_Mode"] == "Index":
            if not re.fullmatch(r"[0-9]+", key):
                raise ValidationError(f"Rule {i}: index must be a zero-based integer")
            key = str(int(key))
        if key in rules:
            raise ValidationError("Duplicate channel rule: " + key)
        if isinstance(radius, bool):
            raise ValidationError("Radius must be a number")
        try:
            token = radius_token(radius)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValidationError(f"Invalid radius in rule {i}") from exc
        if float(token) > limits["max_radius_pixels"]:
            raise ValidationError("Radius exceeds the operator's configured limit")
        rules[key] = token
    p["rules"] = rules
    for key in list(p):
        if key.startswith("Rule_"):
            del p[key]
    p["New_Dataset_Name"] = str(p.get("New_Dataset_Name") or "").strip()
    for key in ("Output_Dataset_ID", "Output_Project_ID"):
        if p.get(key) is not None:
            p[key] = positive_id(p[key], key)
        else:
            p[key] = None
    p["Resume_Run_ID"] = str(p.get("Resume_Run_ID") or "").strip()
    if p["Resume_Run_ID"] and not re.fullmatch(r"[0-9a-f]{32}", p["Resume_Run_ID"]):
        raise ValidationError("Resume_Run_ID must be the 32-character run ID from a report")
    return p


def destination_errors(p, max_name):
    if p["Output_Mode"] == "Existing_Dataset":
        if p["Output_Dataset_ID"] is None:
            return ["Existing_Dataset requires Output_Dataset_ID"]
        if p["New_Dataset_Name"] or p["Output_Project_ID"] is not None:
            return ["Existing_Dataset cannot include a new dataset name/project"]
    else:
        if not p["New_Dataset_Name"]:
            return ["New_Dataset requires New_Dataset_Name (OMERO assigns its ID)"]
        if p["Output_Dataset_ID"] is not None:
            return ["New_Dataset cannot include Output_Dataset_ID"]
        if len(p["New_Dataset_Name"]) > max_name:
            return ["New dataset name exceeds configured OMERO name limit"]
    return []


def matches(name, p):
    pattern = p["Name_Filter"]
    if not pattern:
        return True
    if not p["Case_Sensitive"]:
        name, pattern = name.casefold(), pattern.casefold()
    mode = p["Name_Match_Mode"]
    if mode == "Contains":
        return pattern in name
    if mode == "Exact":
        return pattern == name
    # Linear-space greedy wildcard matching: no user-controlled regex/backtracking.
    i = j = 0
    star = -1
    retry = 0
    while i < len(name):
        if j < len(pattern) and (pattern[j] == "?" or pattern[j] == name[i]):
            i, j = i + 1, j + 1
        elif j < len(pattern) and pattern[j] == "*":
            star, retry, j = j, i, j + 1
        elif star >= 0:
            retry += 1
            i, j = retry, star + 1
        else:
            return False
    return all(ch == "*" for ch in pattern[j:])


def make_plan(snapshots, p, limits):
    rows, errors, warnings = [], [], []
    ordered = sorted(snapshots, key=lambda x: x["id"])
    reference = None
    total_planes = total_bytes = 0
    if len(ordered) > limits["max_images_per_batch"]:
        errors.append("Matched image count exceeds configured batch limit")
    if not p["rules"]:
        errors.append("No channel rules: inventory only; enter a radius for every channel")
    for source in ordered:
        row = {"source": source, "state": "PLANNED", "output_id": None, "errors": []}
        names = [(n or "").strip() for n in source["channel_names"]]
        c = source["c"]
        if reference is None:
            reference = names
        if len(names) != c or names != reference:
            row["errors"].append("Channel count/order differs from reference image")
        if c > 8:
            row["errors"].append("More than eight channels: extend the form before processing")
        if source["dtype"] not in ("uint8", "uint16", "float32"):
            row["errors"].append("Unsupported pixel type: " + source["dtype"])
        if source.get("plate") or source.get("multisample"):
            row["errors"].append("Plate/well or multi-sample RGB input is unsupported")
        if min(source[k] for k in ("x", "y", "z", "c", "t")) < 1:
            row["errors"].append("Invalid dimensions")
        if source["x"] * source["y"] > limits["max_xy_plane_pixels"]:
            row["errors"].append("XY plane exceeds configured whole-plane limit")
        keys = names if p["Channel_Match_Mode"] == "Name" else [str(i) for i in range(c)]
        if p["Channel_Match_Mode"] == "Name" and (not all(names) or len(set(names)) != c):
            row["errors"].append("Blank/duplicate channel names: use Index mode")
        if p["rules"] and set(p["rules"]) != set(keys):
            row["errors"].append("Rules must cover every channel exactly, with no extra rules")
        if p["rules"] and all(k in p["rules"] for k in keys):
            row["radii"] = [p["rules"][k] for k in keys]
            try:
                row["output_name"] = output_name(source["name"], row["radii"], limits["max_name_length"])
            except ValidationError as exc:
                row["errors"].append(str(exc))
        planes = source["z"] * c * source["t"]
        total_planes += planes
        total_bytes += source["x"] * source["y"] * planes * {"uint8": 1, "uint16": 2, "float32": 4}.get(source["dtype"], 8)
        errors.extend(f"Image {source['id']}: {e}" for e in row["errors"])
        rows.append(row)
    if total_planes > limits["max_planes_per_batch"]:
        errors.append("Plane count exceeds configured batch limit")
    if total_bytes > limits["max_output_bytes"]:
        errors.append("Uncompressed outputs exceed configured per-run storage allowance")
    if p["Channel_Match_Mode"] == "Index" and reference and (not all(reference) or len(set(reference)) != len(reference)):
        warnings.append("Unlabelled/duplicate channels: user must ensure biological acquisition order")
    return {"rows": rows, "errors": errors, "warnings": warnings,
            "estimated_output_bytes": total_bytes, "planes": total_planes}


def canonical_config(p):
    return {k: v for k, v in p.items() if k not in ("Run_Mode", "Resume_Run_ID")}
