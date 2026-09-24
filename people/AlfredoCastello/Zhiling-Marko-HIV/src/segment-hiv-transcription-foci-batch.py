"""Batch-segment HIV transcription foci for every image in a folder.

Pick the threshold method in the workbook first, then apply it here. The method (not a fixed
level) is what carries over: the level is recomputed for each image, so exposure differences
between files are handled. The per-file levels land in the CSV -- sort by them, or by
foreground %, to spot images the method treated differently from the rest.

For each input image, writes to the output directory:
  {base}_focimask.tif      uint8 0/255 mask, so Fiji opens it as a binary mask
  {base}_focimask-qc.png   the 4-panel QC plot
  focimask-summary.csv     one row per file: level, foreground %, error

Example:
  uv run python src/segment-hiv-transcription-foci-batch.py /Volumes/Jeff-exFAT/Marko_HIV/110723/MARF1/Dox \
      -o /Volumes/Jeff-exFAT/Marko_HIV/110723/MARF1/Dox/foci_masks -m MaxEntropy --workers 4

Workers process separate images in parallel. Memory use grows with the worker count;
start with 2-4 workers for large stacks. The default is sequential processing.
"""

import argparse
import csv
import sys
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from itertools import repeat
from multiprocessing import get_context
from pathlib import Path

import matplotlib
import numpy as np
import tifffile

matplotlib.use("Agg")  # no display needed; QC figures go straight to PNG
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foci_pipeline import METHOD_NAMES, Params, preprocess, qc_figure, segment  # noqa: E402


def positive_int(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input_dir", type=Path, help="folder holding the images")
    p.add_argument("-o", "--output-dir", type=Path, required=True, help="where masks, QC plots and the CSV go")
    p.add_argument("-p", "--pattern", default="*.tif", help="glob for the input files (default: %(default)s)")
    p.add_argument("-m", "--method", default="MaxEntropy", choices=METHOD_NAMES,
                   help="ImageJ Auto_Threshold method (default: %(default)s)")
    p.add_argument("--channel-axis", type=int, default=1)
    p.add_argument("--channel-index", type=int, default=0)
    p.add_argument("--z-axis", type=int, default=0, help="Z axis after the channel axis is removed")
    p.add_argument("--mode", choices=("2d", "3d"), default="2d",
                   help="2d: project, then filter once (fast). 3d: filter every slice, then "
                        "project (flattest background, ~11x the rolling-ball work). "
                        "Default: %(default)s")
    p.add_argument("--rolling-ball-radius", type=int, default=10)
    p.add_argument("--median-radius", type=int, default=2)
    p.add_argument("-j", "--workers", type=positive_int, default=1,
                   help="number of images processed concurrently; each worker needs memory "
                        "for a stack and QC plot (default: %(default)s)")
    p.add_argument("--dpi", type=int, default=110,
                   help="QC plot resolution; 110 gives a ~1175x1199 px, ~1 MB PNG "
                        "(default: %(default)s)")
    return p.parse_args()


def process_image(path, params, output_dir, method, dpi):
    """Write one image's outputs and return a small, serializable summary row."""
    fig = None
    try:
        raw_projection, input_8bit = preprocess(path, params)
        level, mask = segment(input_8bit, method)
        tifffile.imwrite(output_dir / f"{path.stem}_focimask.tif",
                         (mask * 255).astype(np.uint8))
        fig = qc_figure(raw_projection, input_8bit, mask, method, level)
        fig.savefig(output_dir / f"{path.stem}_focimask-qc.png", dpi=dpi,
                    bbox_inches="tight")
        return [path.name, params.mode, method, level, f"{100 * mask.mean():.4f}", ""]
    except Exception as exc:  # one bad file should not stop the batch
        return [path.name, params.mode, method, "", "", f"{type(exc).__name__}: {exc}"]
    finally:
        if fig is not None:
            plt.close(fig)


def main():
    args = parse_args()
    params = Params(
        channel_axis=args.channel_axis,
        channel_index=args.channel_index,
        z_axis=args.z_axis,
        rolling_ball_radius=args.rolling_ball_radius,
        mode=args.mode,
        median_radius=args.median_radius,
    )

    paths = sorted(args.input_dir.glob(args.pattern))
    if not paths:
        sys.exit(f"no files matching {args.pattern!r} in {args.input_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Files with the same stem would write to the same output, potentially concurrently.
    if len({path.stem for path in paths}) != len(paths):
        sys.exit("input files have duplicate stems; use a narrower --pattern to avoid output collisions")

    workers = min(args.workers, len(paths))
    print(f"Processing {len(paths)} images with {workers} worker(s)", flush=True)
    pool = (ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn"))
            if workers > 1 else nullcontext())

    summary_path = args.output_dir / "focimask-summary.csv"
    with pool as executor, open(summary_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "mode", "method", "threshold_level", "foreground_pct", "error"])

        map_images = executor.map if executor is not None else map
        rows = map_images(process_image, paths, repeat(params), repeat(args.output_dir),
                          repeat(args.method), repeat(args.dpi))
        # Only the parent writes the CSV; map preserves the sorted input order.
        for i, row in enumerate(rows, 1):
            print(f"[{i}/{len(paths)}] {row[0]}", flush=True)
            if row[-1]:
                print(f"    FAILED: {row[-1]}", flush=True)
            else:
                print(f"    level {row[3]}, foreground {float(row[4]):.2f}%", flush=True)
            writer.writerow(row)
            fh.flush()

    print(f"\ndone -> {args.output_dir}\nsummary: {summary_path}")


if __name__ == "__main__":
    main()
