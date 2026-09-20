"""HIV transcription foci segmentation.

The pipeline shared by `segment-hiv-transcription-foci-workbook.ipynb` (which is used to
choose a threshold method) and `segment-hiv-transcription-foci-batch.py` (which applies that choice to a folder).

Steps, in order:
  1. load a multi-dimensional TIF and keep the selected channel
  2. max Z-project the raw channel (kept only for the QC plot)
  3. rolling-ball background subtraction (ImageJ Subtract Background port)
  4. median filter
  5. max Z-project, if it has not happened already (see `Params.mode`)
  6. convert 16-bit -> 8-bit (this is the input to thresholding)
  7. auto-threshold with an ImageJ / Fiji method
  8. fill holes (ImageJ Binary > Fill Holes)
"""

from dataclasses import dataclass

import numpy as np
import tifffile
from matplotlib.colors import ListedColormap
from scipy.ndimage import binary_fill_holes
from skimage.exposure import rescale_intensity
from skimage.filters import median
from skimage.morphology import disk
from skimage.util import img_as_ubyte

from imagej_rolling_ball import rolling_ball_background

import AutoThresholder  # ImageJ / Fiji Auto_Threshold methods, vendored in this folder

# the 17 Auto_Threshold methods, in the plugin's own order
METHOD_NAMES = sorted(
    (name for name in vars(AutoThresholder.Methods) if not name.startswith("__")),
    key=lambda name: getattr(AutoThresholder.Methods, name),
)

CONTRAST_PERCENTILES = (0.5, 99.9)  # display only


@dataclass
class Params:
    """Acquisition layout, filter sizes and where the Z-projection happens.

    Defaults match the workbook.
    """

    channel_axis: int = 1   # 0-based in the order of most likely (Z, C, Y, X)
    channel_index: int = 0  # 0-based: which channel to keep
    z_axis: int = 0         # 0-based: Z axis *after* the channel axis has been removed
    rolling_ball_radius: int = 10  # px; larger than the foci, smaller than cell-scale variation
    median_radius: int = 2         # px; disk(2) is 5x5. Below this the mask is mostly
                                   # single-pixel noise; above it foci erode and merge
    mode: str = "2d"        # "2d": project, then filter once. "3d": filter each slice, then project


# --- thresholding helpers ---------------------------------------------------------------


def histogram_8bit(img):
    """256-bin histogram of an 8-bit image, as the ImageJ methods expect."""
    return np.bincount(np.asarray(img, dtype=np.uint8).ravel(), minlength=256)


def auto_threshold(img, method, hist=None):
    """ImageJ auto-threshold level for an 8-bit image; -1 if the method finds none."""
    if hist is None:
        hist = histogram_8bit(img)
    fn = AutoThresholder.Fx[getattr(AutoThresholder.Methods, method)]
    with np.errstate(divide="ignore", invalid="ignore"):
        # Triangle and IJIsoData edit the histogram in place, so hand them a copy
        return int(fn(hist.copy()))


def all_thresholds(input_8bit, verbose=True):
    """Level picked by every ImageJ method; -1 where the method found or raised nothing."""
    hist = histogram_8bit(input_8bit)
    levels = {}
    for name in METHOD_NAMES:
        try:
            levels[name] = auto_threshold(input_8bit, name, hist)
        except Exception as exc:  # a method can fail outright on a degenerate histogram
            levels[name] = -1
            if verbose:
                print(f"{name}: failed ({type(exc).__name__}: {exc})")
    return levels


def contrast_limits(img, percentiles=CONTRAST_PERCENTILES):
    """(vmin, vmax) for display, from robust percentiles; does not alter the data."""
    lo, hi = np.percentile(img, percentiles)
    return float(lo), float(hi if hi > lo else lo + 1)


# --- pipeline ---------------------------------------------------------------------------


def _clean(img, params):
    """Rolling-ball background subtraction, then median filter, on one 2D image."""
    background = rolling_ball_background(img, params.rolling_ball_radius)
    subtracted = np.clip(img.astype(np.float64) - background, 0, None).astype(img.dtype)
    return median(subtracted, disk(params.median_radius))


def preprocess(image_path, params=Params()):
    """Steps 1-6: returns (raw_projection, input_8bit).

    `raw_projection` is the untouched channel, for the QC plot only; `input_8bit` is the
    image that is actually thresholded.

    Two modes, set by `params.mode`:

    - `"2d"` -- max Z-project, then subtract background and filter the projection once.
    - `"3d"` -- subtract background and filter every slice, then max Z-project: each plane's
      own haze is removed and every slice sits at a zero baseline before they compete in the
      max. Flattest background, ~10x the rolling-ball work.
    """
    # 1. open the image and keep only the selected channel
    raw_stack = tifffile.imread(image_path)
    channel_stack = np.moveaxis(
        np.take(raw_stack, indices=params.channel_index, axis=params.channel_axis),
        params.z_axis, 0,
    )  # (Z, Y, X)

    # 2. max Z-projection of the raw channel, kept only for the QC plot
    raw_projection = np.max(channel_stack, axis=0)

    # 3-5. subtract background and denoise, either side of the projection
    if params.mode == "2d":
        processed = _clean(raw_projection, params)
    elif params.mode == "3d":
        processed = np.max(np.stack([_clean(z, params) for z in channel_stack]), axis=0)
    else:
        raise ValueError(f"mode must be '2d' or '3d', not {params.mode!r}")

    # 6. 16-bit -> 8-bit; this is the input to thresholding
    input_8bit = img_as_ubyte(rescale_intensity(processed, out_range=(0, 1)))
    return raw_projection, input_8bit


def fill_holes(mask):
    """ImageJ Binary > Fill Holes. Holes touching the image border are left, as in ImageJ."""
    return binary_fill_holes(mask)


def segment(input_8bit, method, level=None):
    """Steps 7-8: returns (level, filled boolean mask). Raises if the method finds no level."""
    if level is None:
        level = auto_threshold(input_8bit, method)
    if level < 0:
        raise ValueError(f"{method} found no threshold for this image")
    return level, fill_holes(input_8bit > level)  # Fiji "dark background" convention


def qc_figure(raw_projection, input_8bit, mask, method, level, percentiles=CONTRAST_PERCENTILES):
    """Four panels, all auto-contrasted for display: raw, 8-bit input, mask, overlay."""
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(11, 11))
    axes = axes.ravel()

    vmin, vmax = contrast_limits(raw_projection, percentiles)
    axes[0].imshow(raw_projection, cmap="gray", vmin=vmin, vmax=vmax)
    axes[0].set_title("Raw channel, max Z-projection")

    vmin, vmax = contrast_limits(input_8bit, percentiles)
    axes[1].imshow(input_8bit, cmap="gray", vmin=vmin, vmax=vmax)
    axes[1].set_title("Background-subtracted + median, 8-bit input")

    axes[2].imshow(mask, cmap="gray")
    axes[2].set_title(f"Filled mask ({method} > {level})")

    # orange where the mask is True, fully transparent elsewhere
    orange = ListedColormap(["#ff6600"])
    axes[3].imshow(input_8bit, cmap="gray", vmin=vmin, vmax=vmax)
    axes[3].imshow(np.ma.masked_where(~mask, mask), cmap=orange, alpha=0.9, vmin=0, vmax=1)
    axes[3].set_title("Overlay")

    for ax in axes:
        ax.axis("off")
    fig.tight_layout()
    return fig
