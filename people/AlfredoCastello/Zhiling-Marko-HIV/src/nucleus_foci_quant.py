"""Per-nucleus foci count and FISH intensities, normalised to Mock nuclei.

Used by `measure-foci-nucleus-fish-intensities.ipynb` (section 2). For one image, three
2D inputs of the same shape are matched by basename:
  - nucleus label image (cellpose output; the label value is the nucleus id)
  - foci mask (any non-zero pixel is foreground)
  - sum-projected FISH intensity image (section 1 output)

Rules:
  - nuclei touching the image border are excluded; the remaining ids are kept as-is
  - foci are 4-connected blobs of the foci mask, numbered arbitrarily per image
  - each whole focus goes to the nucleus it overlaps most (any overlap counts); ties go
    to the lowest nucleus id. Majority is decided among all nuclei, so a focus won by an
    excluded border nucleus is dropped, not handed to a neighbour
  - `foci_inclusion` sets which assigned foci are counted:
      "encapsulated" (default): only foci whose every pixel lies inside the assigned
        nucleus; foci reaching outside it (or into another nucleus) are discarded
      "overlapping": every assigned focus, including those reaching outside the nucleus
        (flagged by `foci-extends-outside-nucleus`)
  - focus area and intensity are always those of the whole blob
  - foci that overlap no nucleus are ignored
  - foci smaller than `min_foci_area` pixels are removed before assignment
  - the per-focus table has one row per counted focus plus one row (`has-foci` False, foci
    fields empty) per kept nucleus without foci; the per-nucleus table is summarised from it

Across the dataset, conditions are parsed from the basename
(`{replicate}_{rbp}_{induction}_{hiv-infection}_{field}`), and every raw FISH integrated
intensity is normalised as raw - area x background-per-pixel, where background-per-pixel
comes from the Mock nuclei of the same `group_by` group (see `mock_baselines`). Detected
foci are then flagged `foci-counted` by the Mock foci filter (see `apply_mock_foci_filter`),
and only counted foci enter the per-nucleus counts and totals. Optionally, normalised
intensities are also scaled per replicate to a reference condition (see `reference_factors`).
"""

import math
import re

import numpy as np
from scipy import ndimage

CONDITION_COLUMNS = ["replicate", "rbp", "induction", "hiv-infection", "field"]
CONDITION_VALUES = {
    "rbp": ("CPEB4", "GFP", "MARF1"),
    "induction": ("Dox", "NoDox"),
    "hiv-infection": ("INF-24hpi", "INF-48hpi", "Mock"),
}
BASENAME_PATTERN = re.compile(
    r"(?P<replicate>\d{6})_(?P<rbp>[^_]+)_(?P<induction>[^_]+)_(?P<infection>[^_]+)_(?P<field>\d+)")
MOCK = "Mock"
BASELINE_GROUPABLE = ("replicate", "rbp", "induction")
BASELINE_STATISTICS = ("median", "mean", "pooled-mean")
MOCK_FOCI_FILTERS = ("cutoff", "keep", "drop")
REFERENCE_SCALED = {  # table -> normalised intensity columns scaled to the reference condition
    "foci": ["nucleus-fish-normalised-integrated-intensity", "foci-fish-normalised-integrated-intensity"],
    "nucleus": ["nucleus-fish-normalised-integrated-intensity",
                "total-foci-fish-normalised-integrated-intensity",
                "mean-fish-normalised-integrated-intensity-per-foci"],
}
INFECTION_COLOURS = {"Mock": "#8c8c8c", "INF-24hpi": "#2a78d6", "INF-48hpi": "#eb6834"}

FOUR_CONNECTED = ndimage.generate_binary_structure(2, 1)
FOCI_INCLUSIONS = ("encapsulated", "overlapping")


def border_labels(labels):
    """Ids of the labels touching any image edge."""
    edges = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    return set(np.unique(edges[edges > 0]).tolist())


def measure_image(basename, labels, foci_mask, intensity, min_foci_area=0,
                  foci_inclusion="encapsulated"):
    """Returns (rows, messages, foci_lab, assigned).

    `rows` has one row per counted focus plus one row per kept nucleus without foci
    (`has-foci` False; foci fields NaN/None), with raw intensities only. `messages` are (level, text) pairs for the log. `foci_lab` is the 4-connected foci
    label image and `assigned` maps each foci_lab id to its kept nucleus id or to None
    (too small / outside nuclei / on a border nucleus / not encapsulated); both are for
    the QC figure. Foci with area < `min_foci_area` px are removed (0 or 1 keeps every
    focus). `foci_inclusion` is "encapsulated" (keep only foci lying wholly inside their
    nucleus) or "overlapping" (also keep foci that reach outside it).
    """
    if foci_inclusion not in FOCI_INCLUSIONS:
        raise ValueError(f"foci_inclusion {foci_inclusion!r} is not one of {FOCI_INCLUSIONS}")
    messages = []
    if labels.ndim != 2 or foci_mask.shape != labels.shape or intensity.shape != labels.shape:
        raise ValueError(f"shape mismatch: labels {labels.shape}, foci {foci_mask.shape}, "
                         f"intensity {intensity.shape} (all must be the same 2D shape)")
    if not np.issubdtype(labels.dtype, np.integer):
        raise ValueError(f"label image dtype {labels.dtype} is not integer")
    labels = labels.astype(np.int64)
    intensity = intensity.astype(np.float64)

    # --- nuclei ---------------------------------------------------------------------------
    nucleus_ids = np.unique(labels[labels > 0])
    excluded = border_labels(labels)
    kept = [int(n) for n in nucleus_ids if n not in excluded]
    n_max = int(labels.max())
    nucleus_area = np.bincount(labels.ravel(), minlength=n_max + 1)
    nucleus_intensity = np.bincount(labels.ravel(), weights=intensity.ravel(), minlength=n_max + 1)
    if len(nucleus_ids) == 0:
        messages.append(("WARNING", "label image has no nuclei"))
    elif not kept:
        messages.append(("WARNING", f"all {len(nucleus_ids)} nuclei touch the image border"))

    # --- foci -----------------------------------------------------------------------------
    foci_lab, n_foci = ndimage.label(foci_mask > 0, structure=FOUR_CONNECTED)
    foci_area = np.bincount(foci_lab.ravel(), minlength=n_foci + 1)
    foci_intensity = np.bincount(foci_lab.ravel(), weights=intensity.ravel(), minlength=n_foci + 1)
    rows, cols = np.indices(labels.shape)
    foci_cy = np.bincount(foci_lab.ravel(), weights=rows.ravel(), minlength=n_foci + 1)
    foci_cx = np.bincount(foci_lab.ravel(), weights=cols.ravel(), minlength=n_foci + 1)
    foci_max = (ndimage.maximum(intensity, foci_lab, np.arange(1, n_foci + 1))
                if n_foci else np.array([]))

    # overlap[(focus, nucleus)] = pixel count, from the pixels where both are present
    both = (foci_lab > 0) & (labels > 0)
    pairs, counts = np.unique(np.stack([foci_lab[both], labels[both]]), axis=1, return_counts=True)
    overlaps = {}  # focus -> [(count, nucleus), ...]
    for (f, n), c in zip(pairs.T.tolist(), counts.tolist()):
        overlaps.setdefault(f, []).append((c, n))

    assigned = {}  # focus -> kept nucleus id, or None
    n_small = n_outside = n_on_border = n_not_encapsulated = 0
    for f in range(1, n_foci + 1):
        if foci_area[f] < min_foci_area:
            assigned[f] = None
            n_small += 1
            continue
        cands = overlaps.get(f)
        if not cands:
            assigned[f] = None
            n_outside += 1
            continue
        best = max(c for c, _ in cands)
        winners = sorted(n for c, n in cands if c == best)
        nucleus = winners[0]
        overlap = next(c for c, n in cands if n == nucleus)
        drop_unencapsulated = (foci_inclusion == "encapsulated" and nucleus not in excluded
                               and overlap < foci_area[f])
        if len(cands) > 1:
            detail = ", ".join(f"nucleus {n}: {c} px" for c, n in sorted(cands, key=lambda x: x[1]))
            messages.append(("OUTLIER", f"focus at (x={foci_cx[f] / foci_area[f]:.0f}, "
                                        f"y={foci_cy[f] / foci_area[f]:.0f}) overlaps "
                                        f"{len(cands)} nuclei ({detail}) -> nucleus {nucleus}"
                                        + (" (tie, lowest id)" if len(winners) > 1 else "")
                                        + (" (dropped: not encapsulated)" if drop_unencapsulated else "")))
        if nucleus in excluded:
            assigned[f] = None
            n_on_border += 1
        elif drop_unencapsulated:
            assigned[f] = None
            n_not_encapsulated += 1
        else:
            assigned[f] = nucleus

    # --- rows -----------------------------------------------------------------------------
    def nucleus_fields(n):
        return {
            "image-filename-basename": basename,
            "nucleus-id": n,
            "nucleus-area": int(nucleus_area[n]),
            "nucleus-fish-raw-integrated-intensity": float(nucleus_intensity[n]),
        }

    rows = []
    with_foci = set()
    index = 0
    for f in range(1, n_foci + 1):
        nucleus = assigned[f]
        if nucleus is None:
            continue
        index += 1
        with_foci.add(nucleus)
        overlap = next(c for c, n in overlaps[f] if n == nucleus)
        rows.append({
            **nucleus_fields(nucleus),
            "has-foci": True,
            "foci-index": index,
            "foci-area": int(foci_area[f]),
            "foci-fish-raw-integrated-intensity": float(foci_intensity[f]),
            "foci-max-intensity": float(foci_max[f - 1]),
            "foci-centroid-x": float(foci_cx[f] / foci_area[f]),
            "foci-centroid-y": float(foci_cy[f] / foci_area[f]),
            "foci-extends-outside-nucleus": bool(overlap < foci_area[f]),
        })
    for n in kept:
        if n not in with_foci:
            rows.append({
                **nucleus_fields(n),
                "has-foci": False,
                "foci-index": None,
                "foci-area": math.nan,
                "foci-fish-raw-integrated-intensity": math.nan,
                "foci-max-intensity": math.nan,
                "foci-centroid-x": math.nan,
                "foci-centroid-y": math.nan,
                "foci-extends-outside-nucleus": None,
            })

    messages.append(("INFO", f"{len(nucleus_ids)} nuclei ({len(excluded)} on border, {len(kept)} kept); "
                             f"{n_foci} foci ({index} counted, {n_small} below {min_foci_area} px, "
                             f"{n_on_border} on border nuclei, "
                             f"{n_outside} outside nuclei"
                             + (f", {n_not_encapsulated} not encapsulated"
                                if foci_inclusion == "encapsulated" else "") + ")"))
    return rows, messages, foci_lab, assigned


# --- dataset level: conditions, Mock baselines, normalisation, per-nucleus summary ------------
def baseline_column(statistic):
    return f"mock-nuclear-fish-background-per-pixel-{statistic}"


def cutoff_column(percentile):
    return f"mock-foci-cutoff-p{percentile:g}-normalised-integrated-intensity"


def relative_column(column):
    return f"{column}-relative-to-reference"


def _with_relative(columns, table, relative):
    """Each reference-scaled column followed by its relative column, if `relative`."""
    if not relative:
        return columns
    return [c for col in columns
            for c in ([col, relative_column(col)] if col in REFERENCE_SCALED[table] else [col])]


def foci_columns(statistic, cutoff_percentile=None, relative=False):
    """`cutoff_percentile` adds the cutoff column (Mock foci filter "cutoff" only);
    `relative` adds the reference-scaled columns."""
    cutoff = [cutoff_column(cutoff_percentile)] if cutoff_percentile is not None else []
    return _with_relative([
        "image-filename-basename", *CONDITION_COLUMNS, "nucleus-id", "nucleus-area",
        baseline_column(statistic),
        "nucleus-fish-raw-integrated-intensity", "nucleus-fish-normalised-integrated-intensity",
        "has-foci", "foci-index", "foci-area",
        "foci-fish-raw-integrated-intensity", "foci-fish-normalised-integrated-intensity",
        *cutoff, "foci-counted",
        "foci-max-intensity", "foci-centroid-x", "foci-centroid-y", "foci-extends-outside-nucleus",
    ], "foci", relative)


def nucleus_columns(statistic, cutoff_percentile=None, relative=False):
    cutoff = [cutoff_column(cutoff_percentile)] if cutoff_percentile is not None else []
    return _with_relative([
        "image-filename-basename", *CONDITION_COLUMNS, "nucleus-id", "nucleus-area",
        baseline_column(statistic), *cutoff,
        "nucleus-fish-raw-integrated-intensity", "nucleus-fish-normalised-integrated-intensity",
        "foci-detected-count", "foci-count", "total-foci-area",
        "total-foci-fish-raw-integrated-intensity", "total-foci-fish-normalised-integrated-intensity",
        "mean-fish-raw-integrated-intensity-per-foci", "mean-fish-normalised-integrated-intensity-per-foci",
    ], "nucleus", relative)


def baseline_columns(group_by):
    return [*group_by, "n-mock-nuclei", "n-mock-images", "median", "mean", "pooled-mean", "sd",
            "statistic-used"]


def reference_columns():
    return ["replicate", "rbp", "induction", "hiv-infection", "n-reference-nuclei",
            "n-reference-foci-counted",
            *(f"{c}-reference-mean" for c in REFERENCE_SCALED["nucleus"]),
            "foci-fish-normalised-integrated-intensity-reference-mean"]


def foci_filter_columns(group_by):
    return [*group_by, "mock-foci-filter", "percentile", "cutoff", "n-mock-nuclei",
            "n-mock-foci-detected", "n-mock-foci-counted", "mock-counted-foci-per-nucleus",
            "n-foci-detected", "n-foci-counted"]


def parse_basename(basename):
    """{replicate, rbp, induction, hiv-infection, field} from e.g. 080623_CPEB4_Dox_INF-24hpi_001.

    All values stay strings (replicate and field keep their leading zeros). Raises
    ValueError if the name does not match or a value is not in CONDITION_VALUES.
    """
    m = BASENAME_PATTERN.fullmatch(basename)
    if not m:
        raise ValueError(f"basename {basename!r} does not match "
                         f"{{replicate}}_{{rbp}}_{{induction}}_{{hiv-infection}}_{{field}}, "
                         f"e.g. 080623_CPEB4_Dox_INF-24hpi_001")
    parsed = {"replicate": m["replicate"], "rbp": m["rbp"], "induction": m["induction"],
              "hiv-infection": m["infection"], "field": m["field"]}
    bad = [f"{k} {parsed[k]!r} (expected {' / '.join(v)})"
           for k, v in CONDITION_VALUES.items() if parsed[k] not in v]
    if bad:
        raise ValueError(f"basename {basename!r}: unknown {'; '.join(bad)}")
    return parsed


def _nuclei(rows):
    """{(image, nucleus id): [rows]} in first-seen order; nucleus columns repeat on each row."""
    groups = {}
    for r in rows:
        groups.setdefault((r["image-filename-basename"], r["nucleus-id"]), []).append(r)
    return groups


def mock_baselines(rows, group_by, statistic):
    """{group key: baseline-table row} from the Mock nuclei of each `group_by` group.

    A group key is the tuple of the row's `group_by` values. Per Mock nucleus, density =
    raw integrated intensity / area (per pixel of the sum projection). Each group gets the
    median, mean and sample SD of the densities and the pooled mean sum(intensity) /
    sum(area); `statistic` names the one used for normalisation. Every group present in
    `rows` must have Mock nuclei, otherwise ValueError.
    """
    unknown = [c for c in group_by if c not in BASELINE_GROUPABLE]
    if unknown:
        raise ValueError(f"BASELINE_GROUP_BY {unknown} not in {list(BASELINE_GROUPABLE)}")
    if statistic not in BASELINE_STATISTICS:
        raise ValueError(f"BASELINE_STATISTIC {statistic!r} is not one of {BASELINE_STATISTICS}")
    nuclei = [rs[0] for rs in _nuclei(rows).values()]
    mock = {}
    for r in nuclei:
        if r["hiv-infection"] == MOCK:
            mock.setdefault(tuple(r[c] for c in group_by), []).append(r)
    missing = sorted({tuple(r[c] for c in group_by) for r in nuclei} - mock.keys())
    if missing:
        raise ValueError("no Mock nuclei for " + "; ".join(
            ", ".join(f"{c}={v}" for c, v in zip(group_by, key)) for key in missing)
            + f" (BASELINE_GROUP_BY = {list(group_by)})")

    baselines = {}
    for key, rs in sorted(mock.items()):
        intensity = np.array([r["nucleus-fish-raw-integrated-intensity"] for r in rs])
        area = np.array([r["nucleus-area"] for r in rs], dtype=np.float64)
        density = intensity / area
        baselines[key] = {
            **dict(zip(group_by, key)),
            "n-mock-nuclei": len(rs),
            "n-mock-images": len({r["image-filename-basename"] for r in rs}),
            "median": float(np.median(density)),
            "mean": float(density.mean()),
            "pooled-mean": float(intensity.sum() / area.sum()),
            "sd": float(density.std(ddof=1)) if len(rs) > 1 else math.nan,
            "statistic-used": statistic,
        }
    return baselines


def normalise(rows, baselines, group_by, statistic):
    """Copies of `rows` with the baseline column and normalised = raw - area x baseline.

    Negative values are kept. Rows without foci keep NaN foci intensities.
    """
    column = baseline_column(statistic)
    out = []
    for r in rows:
        b = baselines[tuple(r[c] for c in group_by)][statistic]
        out.append({
            **r,
            column: b,
            "nucleus-fish-normalised-integrated-intensity":
                r["nucleus-fish-raw-integrated-intensity"] - r["nucleus-area"] * b,
            "foci-fish-normalised-integrated-intensity":
                r["foci-fish-raw-integrated-intensity"] - r["foci-area"] * b,
        })
    return out


def apply_mock_foci_filter(rows, group_by, mode, percentile):
    """Returns (copies of `rows` with `foci-counted`, {group key: filter-table row}).

    Run on normalised rows. `mode`:
      "cutoff": per `group_by` group, cutoff = `percentile` of the normalised integrated
                intensity of the foci detected in Mock nuclei; in every condition a focus
                is counted only if its normalised integrated intensity > cutoff. Rows get
                the cutoff column. A group with Mock nuclei but no Mock foci gets no cutoff
                (NaN) and all its foci are counted
      "keep":   every detected focus is counted
      "drop":   foci in Mock nuclei are not counted; all others are
    Rows without foci get `foci-counted` None. Every group must have Mock nuclei,
    otherwise ValueError.
    """
    unknown = [c for c in group_by if c not in BASELINE_GROUPABLE]
    if unknown:
        raise ValueError(f"FOCI_CUTOFF_GROUP_BY {unknown} not in {list(BASELINE_GROUPABLE)}")
    if mode not in MOCK_FOCI_FILTERS:
        raise ValueError(f"MOCK_FOCI_FILTER {mode!r} is not one of {MOCK_FOCI_FILTERS}")
    if mode == "cutoff" and not 0 <= percentile <= 100:
        raise ValueError(f"MOCK_FOCI_CUTOFF_PERCENTILE {percentile} is not within 0-100")

    def key(r):
        return tuple(r[c] for c in group_by)

    mock_nuclei, mock_values = {}, {}
    for r in rows:
        mock_nuclei.setdefault(key(r), set())
        if r["hiv-infection"] == MOCK:
            mock_nuclei[key(r)].add((r["image-filename-basename"], r["nucleus-id"]))
            if r["has-foci"]:
                mock_values.setdefault(key(r), []).append(r["foci-fish-normalised-integrated-intensity"])
    missing = sorted(k for k, nuclei in mock_nuclei.items() if not nuclei)
    if missing:
        raise ValueError("no Mock nuclei for " + "; ".join(
            ", ".join(f"{c}={v}" for c, v in zip(group_by, k)) for k in missing)
            + f" (FOCI_CUTOFF_GROUP_BY = {list(group_by)})")
    cutoffs = {k: (float(np.percentile(mock_values[k], percentile))
                   if mode == "cutoff" and k in mock_values else math.nan)
               for k in mock_nuclei}

    out = []
    for r in rows:
        r = dict(r)
        cutoff = cutoffs[key(r)]
        if mode == "cutoff":
            r[cutoff_column(percentile)] = cutoff
        if not r["has-foci"]:
            r["foci-counted"] = None
        elif mode == "keep":
            r["foci-counted"] = True
        elif mode == "drop":
            r["foci-counted"] = r["hiv-infection"] != MOCK
        else:
            r["foci-counted"] = math.isnan(cutoff) or r["foci-fish-normalised-integrated-intensity"] > cutoff
        out.append(r)

    table = {}
    for k in sorted(mock_nuclei):
        foci = [r for r in out if key(r) == k and r["has-foci"]]
        mock_foci = [r for r in foci if r["hiv-infection"] == MOCK]
        n_mock_counted = sum(r["foci-counted"] for r in mock_foci)
        table[k] = {
            **dict(zip(group_by, k)),
            "mock-foci-filter": mode,
            "percentile": percentile if mode == "cutoff" else math.nan,
            "cutoff": cutoffs[k],
            "n-mock-nuclei": len(mock_nuclei[k]),
            "n-mock-foci-detected": len(mock_foci),
            "n-mock-foci-counted": n_mock_counted,
            "mock-counted-foci-per-nucleus": n_mock_counted / len(mock_nuclei[k]),
            "n-foci-detected": len(foci),
            "n-foci-counted": sum(r["foci-counted"] for r in foci),
        }
    return out, table


def summarise_per_nucleus(rows, statistic, cutoff_percentile=None):
    """One row per nucleus from the normalised, filtered per-focus rows. Counts, totals and
    means use counted foci only (none counted: count 0, totals 0, means NaN)."""
    column = baseline_column(statistic)
    cutoff = [cutoff_column(cutoff_percentile)] if cutoff_percentile is not None else []
    carried = ["image-filename-basename", *CONDITION_COLUMNS, "nucleus-id", "nucleus-area", column,
               *cutoff, "nucleus-fish-raw-integrated-intensity",
               "nucleus-fish-normalised-integrated-intensity"]
    out = []
    for rs in _nuclei(rows).values():
        foci = [r for r in rs if r["has-foci"] and r["foci-counted"]]
        raw = sum((r["foci-fish-raw-integrated-intensity"] for r in foci), 0.0)
        norm = sum((r["foci-fish-normalised-integrated-intensity"] for r in foci), 0.0)
        out.append({
            **{k: rs[0][k] for k in carried},
            "foci-detected-count": sum(bool(r["has-foci"]) for r in rs),
            "foci-count": len(foci),
            "total-foci-area": sum(r["foci-area"] for r in foci),
            "total-foci-fish-raw-integrated-intensity": raw,
            "total-foci-fish-normalised-integrated-intensity": norm,
            "mean-fish-raw-integrated-intensity-per-foci": raw / len(foci) if foci else math.nan,
            "mean-fish-normalised-integrated-intensity-per-foci": norm / len(foci) if foci else math.nan,
        })
    return out


def reference_factors(foci_rows, nucleus_rows, condition):
    """{replicate: reference-table row}: per replicate, the mean of each reference-scaled
    column over the `condition` rows of that replicate.

    `condition` maps parsed columns ("rbp", "induction", "hiv-infection") to values, e.g.
    {"rbp": "GFP", "induction": "NoDox", "hiv-infection": "INF-48hpi"}. Per-nucleus columns
    are averaged over the reference nuclei (NaN skipped, so the mean per focus uses nuclei
    with counted foci); the per-focus `foci-fish-normalised-integrated-intensity` over the
    counted reference foci. Raises ValueError for an unknown condition, a replicate
    without reference nuclei or counted foci, or a mean <= 0.
    """
    bad = [f"{k}={v!r}" for k, v in condition.items()
           if k not in CONDITION_VALUES or v not in CONDITION_VALUES[k]]
    if bad or not condition:
        raise ValueError(f"REFERENCE_CONDITION {condition} has unknown {', '.join(bad) or 'nothing'}; "
                         f"keys and values must come from {CONDITION_VALUES}")
    label = ", ".join(f"{k}={v}" for k, v in condition.items())

    def is_reference(r):
        return all(r[k] == v for k, v in condition.items())

    table = {}
    for rep in sorted({r["replicate"] for r in nucleus_rows}):
        nuclei = [r for r in nucleus_rows if r["replicate"] == rep and is_reference(r)]
        foci = [r["foci-fish-normalised-integrated-intensity"] for r in foci_rows
                if r["replicate"] == rep and is_reference(r) and r["has-foci"] and r["foci-counted"]]
        if not nuclei or not foci:
            raise ValueError(f"replicate {rep} has no {'nuclei' if not nuclei else 'counted foci'} "
                             f"for REFERENCE_CONDITION {label}")
        row = {"replicate": rep, **{k: condition.get(k, "any") for k in ("rbp", "induction", "hiv-infection")},
               "n-reference-nuclei": len(nuclei), "n-reference-foci-counted": len(foci)}
        for c in REFERENCE_SCALED["nucleus"]:
            row[f"{c}-reference-mean"] = float(np.nanmean([r[c] for r in nuclei]))
        row["foci-fish-normalised-integrated-intensity-reference-mean"] = float(np.mean(foci))
        not_positive = [k for k, v in row.items() if k.endswith("-reference-mean") and not v > 0]
        if not_positive:
            raise ValueError(f"replicate {rep}: reference mean <= 0 for {', '.join(not_positive)} "
                             f"(REFERENCE_CONDITION {label})")
        table[rep] = row
    return table


def scale_to_reference(rows, factors, table):
    """Copies of `rows` with each REFERENCE_SCALED[table] column divided by its replicate's
    reference mean, as `{column}-relative-to-reference` (the reference averages 1). The
    per-focus table's nucleus column uses the same reference mean as the per-nucleus one."""
    return [{**r, **{relative_column(c): r[c] / factors[r["replicate"]][f"{c}-reference-mean"]
                     for c in REFERENCE_SCALED[table]}}
            for r in rows]


def foci_intensity_figure(rows, filter_table, group_by, title=""):
    """Distribution of focus normalised integrated intensity, one panel per filter group.

    Every detected focus (counted or not), one step histogram per `hiv-infection`, each
    scaled to its own total so conditions with very different foci numbers compare. x is
    log10, so foci with normalised intensity <= 0 are left out and counted in the legend.
    The cutoff, when there is one, is a dashed line. Figure API, safe from threads.
    """
    from matplotlib.figure import Figure

    keys = list(filter_table)
    foci = [r for r in rows if r["has-foci"]]
    positive = [r["foci-fish-normalised-integrated-intensity"] for r in foci
                if r["foci-fish-normalised-integrated-intensity"] > 0]
    lo, hi = (np.log10(min(positive)), np.log10(max(positive))) if positive else (0.0, 1.0)
    bins = np.linspace(lo, hi if hi > lo else lo + 1, 61)

    ncols = min(3, len(keys)) or 1
    nrows = max(1, math.ceil(len(keys) / ncols))
    fig = Figure(figsize=(4.2 * ncols, 3.2 * nrows + 0.6), layout="constrained")
    axes = fig.subplots(nrows, ncols, squeeze=False, sharex=True)
    for ax in axes.flat[len(keys):]:
        ax.set_visible(False)
    for ax, k in zip(axes.flat, keys):
        group = [r for r in foci if tuple(r[c] for c in group_by) == k]
        for infection, colour in INFECTION_COLOURS.items():  # Mock first, under the infected lines
            values = np.array([r["foci-fish-normalised-integrated-intensity"]
                               for r in group if r["hiv-infection"] == infection])
            if not len(values):
                continue
            pos = values[values > 0]
            weights = np.full(len(pos), 1 / len(values))
            ax.hist(np.log10(pos), bins=bins, weights=weights, histtype="step", linewidth=2,
                    color=colour,
                    label=f"{infection} (n={len(values)}"
                          + (f", {len(values) - len(pos)} <= 0" if len(pos) < len(values) else "") + ")")
        cutoff = filter_table[k]["cutoff"]
        if not math.isnan(cutoff) and cutoff > 0:
            ax.axvline(np.log10(cutoff), color="#333333", linestyle="--", linewidth=1.5,
                       label=f"cutoff p{filter_table[k]['percentile']:g} = {cutoff:.3g}")
        ax.set_title(", ".join(f"{c}={v}" for c, v in zip(group_by, k)) or "all", fontsize=10)
        ax.set_xlabel("log10 focus normalised integrated intensity", fontsize=9)
        ax.set_ylabel("fraction of foci", fontsize=9)
        ax.tick_params(labelsize=8)
        ax.grid(alpha=0.25, linewidth=0.5)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.legend(fontsize=7, frameon=False, loc="upper right")
    if title:
        fig.suptitle(title, fontsize=11)
    return fig


def qc_figure(basename, labels, intensity, foci_lab, assigned, percentiles=(0.5, 99.9)):
    """Sum projection with nucleus and foci outlines at 1:1 pixel scale.

    Kept nuclei cyan with their id, border nuclei grey, counted foci magenta, ignored
    foci yellow (too small, on a border nucleus, outside nuclei or not encapsulated). Uses the Figure API (not pyplot) so it is safe to call from threads.
    """
    from matplotlib.figure import Figure
    from skimage.segmentation import find_boundaries

    lo, hi = np.percentile(intensity, percentiles)
    gray = np.clip((intensity - lo) / max(hi - lo, 1e-12), 0, 1)
    rgb = np.repeat(gray[..., None], 3, axis=2)

    excluded = border_labels(labels)
    nuclei_edges = find_boundaries(labels, mode="inner")
    on_border = np.isin(labels, list(excluded))
    rgb[nuclei_edges & on_border] = (0.5, 0.5, 0.5)
    rgb[nuclei_edges & ~on_border] = (0.0, 1.0, 1.0)

    # foci outlined just outside the blob (so the focus pixels stay visible), 2 px wide
    counted = np.array([False] + [assigned[f] is not None for f in range(1, foci_lab.max() + 1)])
    ring = ndimage.binary_dilation(foci_lab > 0, iterations=2) & (foci_lab == 0)
    nearest = ndimage.grey_dilation(foci_lab, size=5)  # id of the focus each ring pixel surrounds
    rgb[ring & counted[nearest]] = (1.0, 0.0, 1.0)
    rgb[ring & ~counted[nearest]] = (1.0, 1.0, 0.0)

    dpi = 100
    h, w = labels.shape
    fig = Figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(rgb, interpolation="nearest")
    ax.axis("off")
    ids = [n for n in np.unique(labels[labels > 0]).tolist() if n not in excluded]
    for n, (cy, cx) in zip(ids, ndimage.center_of_mass(labels > 0, labels, ids)):
        ax.text(cx, cy, str(n), color="cyan", fontsize=9, ha="center", va="center", weight="bold")
    ax.text(10, 10, f"{basename}\nnuclei: cyan kept (id), grey border-excluded | "
                    f"foci: magenta counted, yellow ignored",
            color="white", fontsize=10, va="top", backgroundcolor=(0, 0, 0, 0.6))
    return fig
