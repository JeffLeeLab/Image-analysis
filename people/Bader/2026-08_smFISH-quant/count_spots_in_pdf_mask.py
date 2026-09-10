# Count smFISH spots falling inside the PDF mask.
# Pairs each spot CSV from smFISH_spot_detection_multiprocess.py with the
# matching PDFmask_*.tif from Bader_generate-pdf-mask.ijm and writes a summary.
# Modified: 2026.08.04

import argparse
import pathlib
import re

import numpy as np
import pandas as pd
import tifffile


def image_stem_from_csv(csv_name):
    """
    Recover the source image stem from a spot CSV filename so that the matching
    mask can be found. The detection script writes '{stem}_ch{N}_spots.csv';
    older runs used '{stem}_ch{N}_bfoutput.csv'.
    """
    stem = pathlib.Path(csv_name).stem
    for pattern in (r"_ch\d+_spots$", r"_ch\d+_bfoutput$", r"_spots$"):
        stripped = re.sub(pattern, "", stem)
        if stripped != stem:
            return stripped
    return stem


def find_mask(mask_dir, image_stem, prefix="PDFmask_"):
    """Return the mask path for an image stem, or None if it doesn't exist."""
    for ext in (".tif", ".tiff"):
        candidate = pathlib.Path(mask_dir).joinpath(f"{prefix}{image_stem}{ext}")
        if candidate.exists():
            return candidate
    return None


def read_mask_zyx(mask_path):
    """
    Read a mask TIFF into a known axis order: (Z, Y, X) for a stack, (Y, X) for
    a single slice.

    The spot coordinates are array indices into a (Z, Y, X) array, so the mask
    has to be brought into the same order rather than assumed. ImageJ can write
    singleton C/T/S dimensions into the hyperstack metadata, so squeeze those
    out by axis *label* (a blind np.squeeze would also drop a genuine Y or X of
    length 1) and refuse anything that isn't recognisable afterwards.
    """
    with tifffile.TiffFile(mask_path) as tif:
        series = tif.series[0]
        mask = series.asarray()
        axes = series.axes

    keep = [
        (axis, size)
        for axis, size in zip(axes, mask.shape)
        if size > 1 or axis in "YX"
    ]
    kept_axes = "".join(axis for axis, _ in keep)
    mask = mask.reshape([size for _, size in keep])

    if not kept_axes.endswith("YX") or len(kept_axes) > 3:
        raise ValueError(
            f"Unexpected mask layout in {mask_path.name}: "
            f"axes={axes!r} shape={mask.shape}. Expected a (Z, Y, X) stack or "
            f"a (Y, X) single slice."
        )

    # A 3-axis mask is a Z stack. tifffile labels the leading axis from the
    # writer's metadata, and ImageJ does not always mark it 'Z' -- 'Q'/'I' are
    # normal for a plain multipage stack. Anything else is accepted (the macro
    # only ever saves a single-channel stack) but flagged, since a genuine
    # multi-channel file here would mean the wrong image was masked.
    if len(kept_axes) == 3 and kept_axes[0] not in "ZQIT":
        print(
            f"  NOTE: {mask_path.name} labels its first axis "
            f"'{kept_axes[0]}'; treating it as Z"
        )

    return mask, kept_axes


def count_inside(spots, mask):
    """
    Count spots whose (z, y, x) coordinate lands on a non-zero mask voxel.

    Returns (nb_inside, nb_total, nb_out_of_bounds). Out-of-bounds coordinates
    are counted as outside rather than clipped: they mean the mask doesn't match
    the image the spots came from, and the caller reports them.
    """
    nb_total = len(spots)
    if nb_total == 0:
        return 0, 0, 0

    # Columns by name, never by position -- the CSV header is authoritative.
    coords = np.floor(spots[["z", "y", "x"]].to_numpy()).astype(np.int64)
    if mask.ndim == 2:
        coords = coords[:, 1:]  # drop z, index the single slice with (y, x)

    in_bounds = np.all((coords >= 0) & (coords < np.array(mask.shape)), axis=1)
    nb_inside = int((mask[tuple(coords[in_bounds].T)] > 0).sum())

    return nb_inside, nb_total, int((~in_bounds).sum())


def main():
    parser = argparse.ArgumentParser(
        description="Count smFISH spots falling inside the matching PDF mask."
    )
    parser.add_argument(
        "--spots-dir", required=True,
        help="bigfish output directory holding the *_spots.csv files (not the qc dir)"
    )
    parser.add_argument(
        "--mask-dir", required=True,
        help="directory holding the PDFmask_*.tif files"
    )
    parser.add_argument(
        "--output", default="pdf_mask_summary.csv",
        help="path of the summary CSV to write (default: pdf_mask_summary.csv)"
    )
    args = parser.parse_args()

    spots_dir = pathlib.Path(args.spots_dir)
    csv_paths = sorted(spots_dir.glob("*.csv"))
    if not csv_paths:
        print(f"No .csv files found in {spots_dir}")
        return

    rows = []
    skipped = []

    for csv_path in csv_paths:
        print(" ")
        print("Processing: ", csv_path.name)

        image_stem = image_stem_from_csv(csv_path.name)
        mask_path = find_mask(args.mask_dir, image_stem)
        if mask_path is None:
            print(f"  no mask found for '{image_stem}', skipping")
            skipped.append(csv_path.name)
            continue

        spots = pd.read_csv(csv_path)
        missing = [c for c in ("z", "y", "x") if c not in spots.columns]
        if missing:
            print(f"  missing column(s) {missing}, skipping")
            skipped.append(csv_path.name)
            continue

        mask, axes = read_mask_zyx(mask_path)
        print(f"  mask {mask_path.name}: axes={axes} shape={mask.shape}")

        nb_inside, nb_total, nb_out = count_inside(spots, mask)
        if nb_out:
            print(
                f"  WARNING: {nb_out}/{nb_total} spots fall outside the mask "
                f"bounds {mask.shape} -- check the mask matches this image"
            )
        print(f"  {nb_inside}/{nb_total} spots within mask")

        rows.append({
            "csv_filename": csv_path.name,
            "nb_spots_within_mask": nb_inside,
        })

    pd.DataFrame(
        rows, columns=["csv_filename", "nb_spots_within_mask"]
    ).to_csv(args.output, index=False)

    print(" ")
    print(f"Wrote {len(rows)} row(s) to {args.output}")
    if skipped:
        print(f"Skipped {len(skipped)} file(s):")
        for name in skipped:
            print(f"  {name}")


if __name__ == "__main__":
    main()
