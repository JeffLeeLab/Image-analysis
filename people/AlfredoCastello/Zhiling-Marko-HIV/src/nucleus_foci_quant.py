"""Per-nucleus foci count and FISH intensities.

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
  - focus area and intensity are those of the whole blob, including pixels outside the
    nucleus (flagged by `foci-extends-outside-nucleus`)
  - foci that overlap no nucleus are ignored
  - foci smaller than `min_foci_area` pixels are removed before assignment
"""

import math

import numpy as np
from scipy import ndimage

FOCI_COLUMNS = [
    "image-filename-basename", "nucleus-id", "nucleus-area", "nucleus-fish-integrated-intensity",
    "foci-index", "foci-area", "foci-fish-integrated-intensity", "foci-max-intensity",
    "foci-centroid-x", "foci-centroid-y", "foci-extends-outside-nucleus",
]
NUCLEUS_COLUMNS = [
    "image-filename-basename", "nucleus-id", "nucleus-area", "nucleus-fish-integrated-intensity",
    "foci-count", "total-foci-area", "total-foci-fish-integrated-intensity",
    "mean-fish-integrated-intensity-per-foci",
]

FOUR_CONNECTED = ndimage.generate_binary_structure(2, 1)


def border_labels(labels):
    """Ids of the labels touching any image edge."""
    edges = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    return set(np.unique(edges[edges > 0]).tolist())


def measure_image(basename, labels, foci_mask, intensity, min_foci_area=0):
    """Returns (foci_rows, nucleus_rows, messages, foci_lab, assigned).

    `messages` are (level, text) pairs for the log. `foci_lab` is the 4-connected foci
    label image and `assigned` maps each foci_lab id to its kept nucleus id or to None
    (too small / outside nuclei / on a border nucleus); both are for the QC figure.
    Foci with area < `min_foci_area` px are removed (0 or 1 keeps every focus).
    """
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
    n_small = n_outside = n_on_border = 0
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
        if len(cands) > 1:
            detail = ", ".join(f"nucleus {n}: {c} px" for c, n in sorted(cands, key=lambda x: x[1]))
            messages.append(("OUTLIER", f"focus at (x={foci_cx[f] / foci_area[f]:.0f}, "
                                        f"y={foci_cy[f] / foci_area[f]:.0f}) overlaps "
                                        f"{len(cands)} nuclei ({detail}) -> nucleus {nucleus}"
                                        + (" (tie, lowest id)" if len(winners) > 1 else "")))
        if nucleus in excluded:
            assigned[f] = None
            n_on_border += 1
        else:
            assigned[f] = nucleus

    # --- rows -----------------------------------------------------------------------------
    foci_rows = []
    per_nucleus = {n: [] for n in kept}
    index = 0
    for f in range(1, n_foci + 1):
        nucleus = assigned[f]
        if nucleus is None:
            continue
        index += 1
        overlap = next(c for c, n in overlaps[f] if n == nucleus)
        row = {
            "image-filename-basename": basename,
            "nucleus-id": nucleus,
            "nucleus-area": int(nucleus_area[nucleus]),
            "nucleus-fish-integrated-intensity": float(nucleus_intensity[nucleus]),
            "foci-index": index,
            "foci-area": int(foci_area[f]),
            "foci-fish-integrated-intensity": float(foci_intensity[f]),
            "foci-max-intensity": float(foci_max[f - 1]),
            "foci-centroid-x": float(foci_cx[f] / foci_area[f]),
            "foci-centroid-y": float(foci_cy[f] / foci_area[f]),
            "foci-extends-outside-nucleus": bool(overlap < foci_area[f]),
        }
        foci_rows.append(row)
        per_nucleus[nucleus].append(row)

    nucleus_rows = []
    for n in kept:
        foci = per_nucleus[n]
        total = sum((r["foci-fish-integrated-intensity"] for r in foci), 0.0)
        nucleus_rows.append({
            "image-filename-basename": basename,
            "nucleus-id": n,
            "nucleus-area": int(nucleus_area[n]),
            "nucleus-fish-integrated-intensity": float(nucleus_intensity[n]),
            "foci-count": len(foci),
            "total-foci-area": sum(r["foci-area"] for r in foci),
            "total-foci-fish-integrated-intensity": total,
            "mean-fish-integrated-intensity-per-foci": total / len(foci) if foci else math.nan,
        })

    messages.append(("INFO", f"{len(nucleus_ids)} nuclei ({len(excluded)} on border, {len(kept)} kept); "
                             f"{n_foci} foci ({len(foci_rows)} counted, {n_small} below {min_foci_area} px, "
                             f"{n_on_border} on border nuclei, "
                             f"{n_outside} outside nuclei)"))
    return foci_rows, nucleus_rows, messages, foci_lab, assigned


def qc_figure(basename, labels, intensity, foci_lab, assigned, percentiles=(0.5, 99.9)):
    """Sum projection with nucleus and foci outlines at 1:1 pixel scale.

    Kept nuclei cyan with their id, border nuclei grey, counted foci magenta, ignored
    foci yellow. Uses the Figure API (not pyplot) so it is safe to call from threads.
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
