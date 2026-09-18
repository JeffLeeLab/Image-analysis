"""ImageJ-equivalent rolling-ball background, vendored from the RDI-calculator project.

Source: `rdi/background.py` in /Users/jefflee/Documents/Incubator/RDI-calculator
(commit d30fae7). Only the rolling-ball half is copied here; the empty-area offset and
`correct_image` are not used by this pipeline.

A faithful port of ImageJ's Process > Subtract Background (rolling-ball mode,
`BackgroundSubtracter.java`): optional 3x3 mean presmooth, block-minimum shrink by a
radius-dependent factor, grayscale opening with a trimmed ball patch, bilinear enlarge.

`skimage.restoration.rolling_ball` is not equivalent: it returns the height of the ball
*centre* (an erosion plus the radius) rather than the ball surface, and has no presmooth
or shrink step.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi

_IMAGEJ_SHRINK_TABLE = (
    (10, 1, 24),
    (30, 2, 24),
    (100, 4, 32),
    (np.inf, 8, 40),
)


# --------------------------------------------------------------------------
# Rolling ball (ImageJ port)
# --------------------------------------------------------------------------


def imagej_shrink_factor(radius: float) -> int:
    """Shrink factor ImageJ picks for ``radius`` (1, 2, 4 or 8)."""
    for max_r, shrink, _ in _IMAGEJ_SHRINK_TABLE:
        if radius <= max_r:
            return shrink
    raise AssertionError("unreachable")


def _arc_trim_percent(radius: float) -> int:
    for max_r, _, trim in _IMAGEJ_SHRINK_TABLE:
        if radius <= max_r:
            return trim
    raise AssertionError("unreachable")


def rolling_ball_patch(radius: float, shrink_factor: int) -> np.ndarray:
    """ImageJ's ``RollingBall.buildRollingBall``: a square patch from the top of a sphere.

    Heights ``sqrt(r_small^2 - x^2 - y^2)`` on a ``(2*half_width+1)`` square
    with ``half_width = round(r_small - trim)``; points outside the sphere get
    height 0 (ImageJ's choice, kept for parity). ``r_small = radius / shrink_factor``
    (min 1). Only the arc-trim percentage depends on the *requested* radius,
    as in ImageJ.
    """
    trim_per = _arc_trim_percent(radius)
    small_r = max(radius / shrink_factor, 1.0)
    xtrim = int(trim_per * small_r) // 100
    half_width = int(round(small_r - xtrim))
    width = 2 * half_width + 1
    yy, xx = np.mgrid[-half_width : half_width + 1, -half_width : half_width + 1]
    temp = small_r * small_r - xx * xx - yy * yy
    patch = np.where(temp > 0, np.sqrt(np.clip(temp, 0, None)), 0.0)
    assert patch.shape == (width, width)
    return patch.astype(np.float64)


def _presmooth_3x3(image: np.ndarray) -> np.ndarray:
    """ImageJ ``filter3x3(fp, MEAN)``: separable 3-point mean, edge pixels repeated."""
    out = ndi.uniform_filter1d(image, size=3, axis=0, mode="nearest")
    out = ndi.uniform_filter1d(out, size=3, axis=1, mode="nearest")
    return out


def _shrink_min(image: np.ndarray, factor: int) -> np.ndarray:
    """ImageJ ``shrinkImage``: each small pixel is the min of its ``factor x factor`` block."""
    h, w = image.shape
    sh, sw = -(-h // factor), -(-w // factor)
    padded = np.full((sh * factor, sw * factor), np.inf, dtype=np.float64)
    padded[:h, :w] = image
    return padded.reshape(sh, factor, sw, factor).min(axis=(1, 3))


def _roll_ball(image: np.ndarray, patch: np.ndarray) -> np.ndarray:
    """ImageJ ``rollBall``: grayscale opening with the non-flat ``patch``.

    Points outside the image do not take part in either the erosion (pad
    with +inf) or the dilation (pad with -inf), which is how ImageJ clips the
    patch at the image border.
    """
    pad = patch.shape[0] // 2
    padded = np.pad(image, pad, mode="constant", constant_values=np.inf)
    eroded = ndi.grey_erosion(padded, structure=patch, mode="constant", cval=np.inf)
    dilated = ndi.grey_dilation(eroded, structure=patch, mode="constant", cval=-np.inf)
    return dilated[pad:-pad, pad:-pad]


def _interp_arrays(length: int, small_length: int, factor: int):
    """ImageJ ``makeInterpolationArrays``: left index and its weight for each full-res pixel."""
    i = np.arange(length)
    small_index = (i - factor // 2) // factor
    small_index = np.minimum(small_index, small_length - 2)
    small_index = np.maximum(small_index, 0)
    distance = (i + 0.5) / factor - (small_index + 0.5)
    weights = 1.0 - distance
    return small_index, weights


def _enlarge(small: np.ndarray, shape: tuple[int, int], factor: int) -> np.ndarray:
    """ImageJ ``enlargeImage``: bilinear interpolation between small-pixel centres (extrapolates at edges)."""
    h, w = shape
    sh, sw = small.shape
    if sh < 2 or sw < 2:
        return np.broadcast_to(small[:1, :1], shape).astype(np.float64)
    yi, wy = _interp_arrays(h, sh, factor)
    xi, wx = _interp_arrays(w, sw, factor)
    row0 = small[yi, :]
    row1 = small[yi + 1, :]
    lines0 = row0[:, xi] * wx + row0[:, xi + 1] * (1.0 - wx)
    lines1 = row1[:, xi] * wx + row1[:, xi + 1] * (1.0 - wx)
    return lines0 * wy[:, None] + lines1 * (1.0 - wy[:, None])


def _rolling_ball_2d(plane: np.ndarray, radius: float, *, presmooth: bool, shrink_factor: int) -> np.ndarray:
    fp = np.asarray(plane, dtype=np.float64)
    if presmooth:
        fp = _presmooth_3x3(fp)
    h, w = fp.shape
    if shrink_factor > 1 and (h // shrink_factor < 2 or w // shrink_factor < 2):
        shrink_factor = 1  # too small to shrink meaningfully
    patch = rolling_ball_patch(radius, shrink_factor)
    small = _shrink_min(fp, shrink_factor) if shrink_factor > 1 else fp
    small_bg = _roll_ball(small, patch)
    if shrink_factor > 1:
        return _enlarge(small_bg, (h, w), shrink_factor)
    return small_bg


def rolling_ball_background(
    image: np.ndarray,
    radius: float,
    *,
    light_background: bool = False,
    presmooth: bool = True,
    shrink: int | None = None,
) -> np.ndarray:
    """Estimate the background image the way ImageJ's rolling ball does.

    Parameters
    ----------
    image : ndarray
        2D ``(y, x)`` or 3D ``(z, y, x)``; a 3D stack is processed one
        z-plane at a time (ImageJ processes stacks slice by slice).
    radius : float
        Ball radius in xy pixels, as in ImageJ. Larger than the largest
        foreground object you want to keep; ~100–150 px for whole cells at
        typical magnification.
    light_background : bool
        Dark features on a bright background (ImageJ "Light background"):
        the image is negated before and after.
    presmooth : bool
        ImageJ's default 3x3 mean before estimating (uncheck "Disable
        smoothing"). The background is estimated on the smoothed image but
        subtracted from the raw one, so a few raw pixels can end up below 0
        and get clipped.
    shrink : int, optional
        Override ImageJ's automatic shrink factor (1 for r<=10, 2 for r<=30,
        4 for r<=100, else 8). ``1`` disables shrinking and is slow for large
        radii.

    Returns
    -------
    background : float64 ndarray, same shape as ``image``.
    """
    image = np.asarray(image)
    if radius is None or not np.isfinite(radius) or radius <= 0:
        raise ValueError(f"radius must be a positive number of pixels, got {radius!r}")
    if image.ndim not in (2, 3):
        raise ValueError(f"image must be 2D (y, x) or 3D (z, y, x), got ndim={image.ndim}")
    factor = imagej_shrink_factor(radius) if shrink is None else int(shrink)
    if factor < 1:
        raise ValueError(f"shrink must be >= 1, got {shrink!r}")

    sign = -1.0 if light_background else 1.0
    work = sign * image.astype(np.float64)
    if image.ndim == 2:
        bg = _rolling_ball_2d(work, radius, presmooth=presmooth, shrink_factor=factor)
    else:
        bg = np.stack(
            [_rolling_ball_2d(plane, radius, presmooth=presmooth, shrink_factor=factor) for plane in work],
            axis=0,
        )
    return sign * bg


def subtract_background_image(image: np.ndarray, background) -> tuple[np.ndarray, float]:
    """``image - background``, negatives clipped to 0 (spec.md §8.2). Returns (float64 image, frac_clipped)."""
    corrected = np.asarray(image, dtype=np.float64) - background
    if corrected.size == 0:
        return corrected, float("nan")
    frac_clipped = float(np.mean(corrected < 0))
    return np.clip(corrected, 0, None), frac_clipped


