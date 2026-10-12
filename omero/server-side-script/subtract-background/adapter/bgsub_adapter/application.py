"""Batch orchestration. Called only after OMERO parameter discovery completes."""
import csv
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import uuid

import Ice
import numpy as np
import omero
from omero.rtypes import rlong, robject, rstring

from . import __version__, ALGORITHM_COMMIT, ALGORITHM_SHA256, NAMESPACE
from .planning import parse_inputs, destination_errors, matches, make_plan, canonical_config, digest, ValidationError
from .runtime import load_config, limit_wrapper_memory, admission, Journal, Worker, SystemicError
from .omero_io import OmeroIO, plane_hash


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def stop_on_signal(signum, frame):
    raise InterruptedError("Script received cancellation signal " + str(signum))


def systemic(exc):
    # Treat remote-service errors conservatively: repeated writes during a
    # storage/session/database problem are unsafe. Local bad-pixel errors may continue.
    return isinstance(exc, (SystemicError, OSError, MemoryError, Ice.LocalException,
                            omero.ServerError))


def check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise SystemicError("Batch elapsed-time limit reached")


def safe_cell(value):
    text = str(value if value is not None else "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


def report(client, io, journal, cfg):
    data = journal.data
    data["counts"] = {state: sum(row["state"] == state for row in data["plan"]["rows"])
                      for state in ("PLANNED", "COMPLETE", "FAILED", "CANCELLED", "NOT_ATTEMPTED")}
    data["counts"].update({"selected": data.get("selected_count", len(data["plan"]["rows"])),
                           "matched": len(data["plan"]["rows"]),
                           "filtered_out": len(data.get("filtered_out", []))})
    journal.save()
    summary = (f"Run {data['run_id']}: {data['status']}. "
               f"Matched {data['counts']['matched']}; complete {data['counts']['COMPLETE']}; "
               f"failed {data['counts']['FAILED']}. "
               + " ".join(data["plan"]["errors"][:8]) + " " + data.get("fatal_error", ""))
    client.setOutput("Run_ID", rstring(data["run_id"]))
    if data.get("destination_id"):
        dataset = io.get("Dataset", data["destination_id"])
        client.setOutput("Output_Dataset_ID", rlong(dataset.getId()))
        client.setOutput("Dataset", robject(dataset._obj))
        summary += f" Output dataset: {dataset.getId()}."
    client.setOutput("Message", rstring(summary))
    try:
        with tempfile.TemporaryDirectory(prefix="bgsub-report-", dir=cfg["temporary_root"]) as directory:
            root = Path(directory)
            json_path = root / (data["run_id"] + ".json")
            json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            csv_path = root / (data["run_id"] + ".csv")
            with csv_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["source_id", "source_name", "output_id", "output_name", "dataset_id",
                                 "group_id", "X", "Y", "Z", "C", "T", "dtype", "channel_names",
                                 "radii", "state", "reason"])
                for row in data["plan"]["rows"]:
                    values = [row["source"]["id"], row["source"]["name"], row.get("output_id"),
                              row.get("output_name"), data.get("destination_id"), row["source"]["group_id"],
                              *[row["source"][axis] for axis in "xyzct"], row["source"]["dtype"],
                              json.dumps(row["source"]["channel_names"], ensure_ascii=False),
                              ";".join(row.get("radii", [])), row["state"],
                              row.get("error", "; ".join(row["errors"]))]
                    writer.writerow([safe_cell(v) for v in values])
                for item in data.get("filtered_out", []):
                    writer.writerow([safe_cell(item["id"]), safe_cell(item["name"])] + [""] * 12
                                    + ["FILTERED_OUT", "Name filter did not match"])
            for label, path, mime in (("JSON_Report", json_path, "application/json"),
                                      ("CSV_Report", csv_path, "text/csv")):
                ann = io.conn.createFileAnnfromLocalFile(str(path), origFilePathAndName=path.name,
                                                        mimetype=mime, ns=NAMESPACE + "/report")
                client.setOutput(label, robject(ann._obj))
                # Preview deliberately does not annotate images or datasets.
                if data.get("destination_id") and data["config_run_mode"] == "Process":
                    io.get("Dataset", data["destination_id"]).linkAnnotation(ann)
    except Exception as exc:
        data["report_error"] = type(exc).__name__ + ": " + str(exc)[:2000]
        journal.save()
        client.setOutput("Message", rstring(summary + " Report upload/link failed; administrator can retrieve journal " + data["run_id"]))


def process_image(io, row, journal, cfg, deadline):
    attempt_start = time.monotonic()
    check_deadline(deadline)
    source = row["source"]
    current = io.snapshot(io.get("Image", source["id"]))
    if digest(current) != digest(source):
        raise ValidationError("Source metadata changed since preflight; start a new run")
    if row["state"] == "COMPLETE":
        io.verify_complete(row, journal)
        return
    output = io.ensure_output(row, journal)
    # Never rewrite an incomplete object if it has already been exposed by a prior link.
    # Once all planes are durably journalled, resume verifies them and finalizes only.
    upload_done = bool(row.get("upload_complete"))
    if not upload_done and list(output.listParents()):
        raise SystemicError("Incomplete image is already linked; operator reconciliation required")
    row["state"] = "PROCESSING"
    row["attempt_started_at"] = utc_now()
    row.pop("error", None)
    journal.save()
    io.provenance(output, {"run_id": journal.data["run_id"], "source_id": source["id"],
                           "fingerprint": row["fingerprint"], "state": "PROCESSING"})
    if not upload_done:
        ranges = [[float("inf"), float("-inf")] for _ in range(source["c"])]
        stats = [{"pixels": 0, "clipped_pixels": 0, "saturated_pixels": 0, "shrink_factors": []}
                 for _ in range(source["c"])]
        hashes = {}
        source_pixels = io.get("Image", source["id"]).getPrimaryPixels()
        store = io.conn.c.sf.createRawPixelsStore()
        try:
            store.setPixelsId(output.getPixelsId(), True, io.opts)
            with Worker(cfg, deadline) as worker:
                row["worker"] = worker.info
                journal.save()
                for z in range(source["z"]):
                    for c in range(source["c"]):
                        for t in range(source["t"]):
                            check_deadline(deadline)
                            plane = source_pixels.getPlane(z, c, t)
                            if plane.shape != (source["y"], source["x"]) or plane.dtype.name != source["dtype"]:
                                raise ValidationError("Raw plane has unexpected shape/dtype")
                            result, summary = worker.plane(plane, row["radii"][c])
                            if float(row["radii"][c]) == 0 and not np.array_equal(plane, result):
                                raise SystemicError("Worker changed a bypassed channel")
                            wire = np.ascontiguousarray(result, dtype=result.dtype.newbyteorder(">"))
                            store.setPlane(wire.tobytes(), z, c, t, io.opts)
                            hashes[f"{z},{c},{t}"] = plane_hash(result)
                            ranges[c][0] = min(ranges[c][0], float(np.min(result)))
                            ranges[c][1] = max(ranges[c][1], float(np.max(result)))
                            for key in ("pixels", "clipped_pixels", "saturated_pixels"):
                                stats[c][key] += summary[key]
                            stats[c]["shrink_factors"] = sorted(set(stats[c]["shrink_factors"] + [summary["shrink"]]))
                            del plane, result, wire
            # close commits server pixel state; only then record a completed upload.
            store.close(io.opts)
            store = None
        finally:
            if store is not None:
                store.close(io.opts)
        row.update({"ranges": ranges, "stats": stats, "plane_hashes": hashes,
                    "upload_complete": True, "state": "PIXELS_WRITTEN"})
        journal.save()
    for c, bounds in enumerate(row["ranges"]):
        io.conn.getPixelsService().setChannelGlobalMinMax(output.getPixelsId(), c, *bounds, io.opts)
    io.verify_pixels(output, row["plane_hashes"])
    io.copy_metadata(output, source)
    io.rendering(output, source, row["ranges"])
    row["state"] = "METADATA_WRITTEN"
    journal.save()
    io.verify_geometry(output, source)
    io.verify_metadata(output, source)
    row["state"] = "VERIFIED"
    journal.save()
    payload = {"schema": 1, "run_id": journal.data["run_id"], "source": source,
               "output_id": output.getId(), "destination_id": journal.data["destination_id"],
               "fingerprint": row["fingerprint"], "radii": row["radii"], "state": "VERIFIED",
               "algorithm_commit": ALGORITHM_COMMIT, "algorithm_sha256": ALGORITHM_SHA256,
               "package_version": __version__, "worker": row["worker"],
               "wrapper": journal.data["wrapper"], "user_id": io.user_id,
               "settings": {"presmooth": True, "shrink": "automatic", "light_background": False,
                            "clip_corrected_negatives": True, "radius_units": "xy_pixels",
                            "output_dtype": source["dtype"], "integer_rounding": "half_up"},
               "statistics": row["stats"], "started_at": journal.data["started_at"]}
    io.provenance(output, payload)
    io.link_output(output, journal.data["destination_id"])
    row["state"] = "LINKED"
    journal.save()
    payload.update({"state": "COMPLETE", "completed_at": utc_now()})
    io.provenance(output, payload)
    row["state"], row["completed_at"] = "COMPLETE", payload["completed_at"]
    row["attempt_elapsed_seconds"] = time.monotonic() - attempt_start
    journal.save()


def execute(client, conn, config_path):
    os.umask(0o077)
    cfg = load_config(config_path)
    limit_wrapper_memory(cfg)
    deadline = time.monotonic() + cfg["max_batch_seconds"]
    p = parse_inputs(client.getInputs(unwrap=True), cfg)
    io = OmeroIO(conn, cfg)
    # One shared lock also prevents a Preview/resume from racing journal writes.
    with admission(cfg["journal_root"]):
        if p["Resume_Run_ID"]:
            journal = Journal(cfg["journal_root"], p["Resume_Run_ID"])
            data = journal.data
            if data["user_id"] != io.user_id or data["group_id"] != io.group_id:
                raise ValidationError("Resume requires the original user and active group")
            if data["config"] != canonical_config(p) or data["version"] != __version__:
                raise ValidationError("Resume parameters/package differ from original run")
            if data["config_run_mode"] != "Process" or data["plan"]["errors"]:
                raise ValidationError("Only a processing run that passed preflight can resume")
            io.validate_destination(p)
            for row in data["plan"]["rows"]:
                if digest(io.snapshot(io.get("Image", row["source"]["id"]))) != digest(row["source"]):
                    raise ValidationError("Source metadata changed; cannot resume")
        else:
            selected_count, filtered, snapshots = 0, [], []
            seen = set()
            for image in io.select(p):
                check_deadline(deadline)
                if image.getId() in seen:
                    continue
                seen.add(image.getId())
                selected_count += 1
                if not matches(image.getName(), p):
                    filtered.append({"id": image.getId(), "name": image.getName(), "state": "FILTERED_OUT"})
                    continue
                if len(snapshots) >= cfg["max_images_per_batch"]:
                    raise ValidationError("Matched image count exceeds operator limit")
                snapshots.append(io.snapshot(image))
            plan = make_plan(snapshots, p, cfg)
            plan["errors"].extend(destination_errors(p, cfg["max_name_length"]))
            if not destination_errors(p, cfg["max_name_length"]):
                try:
                    io.validate_destination(p)
                except ValidationError as exc:
                    plan["errors"].append(str(exc))
            if p["Data_Type"] == "Dataset":
                plan["warnings"].append("Existing derived images matching this dataset/filter are included; input IDs are frozen before output creation")
            plan["warnings"].append("Physical calibration is preserved per source; equal pixel radii may have different physical scales")
            run_id = uuid.uuid4().hex
            journal = Journal(cfg["journal_root"], run_id, {
                "schema": 1, "run_id": run_id, "version": __version__, "config": canonical_config(p),
                "config_run_mode": p["Run_Mode"], "user_id": io.user_id, "group_id": io.group_id,
                "started_at": utc_now(), "status": "PLANNED", "destination_id": None,
                "plan": plan, "selected_count": selected_count, "filtered_out": filtered,
                "wrapper": {"python": sys.version.split()[0], "omero_py": importlib.metadata.version("omero-py"),
                            "Ice": Ice.stringVersion(), "numpy": np.__version__},
                "operator_limits": {k: v for k, v in cfg.items() if k.startswith("max_")}})
            for row in plan["rows"]:
                row["fingerprint"] = digest({"source": row["source"], "radii": row.get("radii"),
                                             "version": __version__, "algorithm": ALGORITHM_SHA256})
            journal.save()
        data = journal.data
        if p["Run_Mode"] == "Preview" or data["plan"]["errors"] or not data["plan"]["rows"]:
            data["status"] = "PREVIEW" if p["Run_Mode"] == "Preview" else ("NO_MATCHES" if not data["plan"]["rows"] else "PREFLIGHT_FAILED")
            report(client, io, journal, cfg)
            return
        original_handlers = {sig: signal.signal(sig, stop_on_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
        fatal = None
        try:
            data["status"] = "PROCESSING"
            data.pop("fatal_error", None)
            journal.save()
            io.destination(p, journal)
            for index, row in enumerate(data["plan"]["rows"]):
                print(f"Image {index + 1}/{len(data['plan']['rows'])}: source={row['source']['id']}", flush=True)
                was_complete = row["state"] == "COMPLETE"
                try:
                    process_image(io, row, journal, cfg, deadline)
                except InterruptedError as exc:
                    if not was_complete:
                        row["state"] = "CANCELLED"
                    row["error"] = str(exc)
                    raise
                except Exception as exc:
                    if row["state"] != "COMPLETE":
                        row["state"] = "FAILED"
                    row["error"] = type(exc).__name__ + ": " + str(exc)[:2000]
                    journal.save()
                    if was_complete or systemic(exc):
                        raise
                    print(row["error"], flush=True)
        except (Exception, KeyboardInterrupt) as exc:
            fatal = type(exc).__name__ + ": " + str(exc)[:2000]
            data["fatal_error"] = fatal
        finally:
            for sig, handler in original_handlers.items():
                signal.signal(sig, handler)
            for row in data["plan"]["rows"]:
                if row["state"] == "PLANNED":
                    row["state"] = "NOT_ATTEMPTED"
            completed = sum(row["state"] == "COMPLETE" for row in data["plan"]["rows"])
            data["status"] = ("SUCCESS" if completed == len(data["plan"]["rows"]) and fatal is None
                              else "PARTIAL_FAILURE" if completed else "FAILED")
            data["ended_at"] = utc_now()
            journal.save()
        report(client, io, journal, cfg)
