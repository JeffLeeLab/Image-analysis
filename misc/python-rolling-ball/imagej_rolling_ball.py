"""ImageJ's rolling-ball background subtraction, ported to Python (numpy + scipy).

Original: https://github.com/JeffLeeLab/Image-analysis/tree/main/misc/python-rolling-ball
Copies in other projects are vendored from there. Make fixes in the original,
where the ImageJ parity tests live, and copy the file again; don't edit copies.

Reproduces ImageJ 1.x *Process > Subtract Background...* in rolling-ball mode
(``ij/plugin/filter/BackgroundSubtracter.java``) step for step:

1. optional 3x3 mean "presmooth" (ImageJ's default: "Disable smoothing" unticked);
2. shrink the image by a radius-dependent factor, each small pixel being the
   minimum of its block;
3. roll a trimmed ball patch under the shrunken image, i.e. a grayscale opening
   with a non-flat structuring element;
4. bilinearly enlarge the result back to full size.

Checked against ImageJ 1.54p: backgrounds agree to float32 rounding (within
1e-6 of the image range) for radii 1-250 px, with and without presmoothing and
light background, and 8-/16-bit subtracted images agree to within one grey
level. The check is in ``tests/`` next to this file (see ``README.md``).

Dependencies: numpy and scipy only (tested with Python 3.9 + numpy 1.24 +
scipy 1.10, and Python 3.13 + numpy 2.4 + scipy 1.18). Copy this one file into
a project to use it.

Usage
-----
    import numpy as np, tifffile
    from imagej_rolling_ball import rolling_ball_background, subtract_background

    img = tifffile.imread("cells.tif")              # (y, x), (z, y, x), (c, z, y, x), ...
    out = subtract_background(img, radius=50)       # ImageJ's result, same dtype as img
    bg = rolling_ball_background(img, radius=50)    # "Create background (don't subtract)"
    raw = subtract_background(img.astype(np.float32), radius=50)  # as ImageJ on 32-bit: no clipping

ImageJ dialog option -> argument
--------------------------------
Rolling ball radius    ``radius`` (pixels)
Light background       ``light_background=True``
Create background      ``rolling_ball_background`` instead of ``subtract_background``
Disable smoothing      ``presmooth=False``
Separate colors        not ported (RGB); split the channels and pass them as planes
Sliding paraboloid     not ported
Correct corners        paraboloid only; not applicable

Why not ``skimage.restoration.rolling_ball``? It returns, for each pixel, how
high the ball's apex can sit when centred there (an erosion), rather than the
hull of all ball positions (the opening ImageJ computes), and it has no
presmooth or shrink step. Its background is lower than ImageJ's and its
results do not match ImageJ's.

Differences from ImageJ
-----------------------
- Arithmetic is float64 (ImageJ uses float32), so backgrounds differ by about
  1e-7 of the image range, and an 8-/16-bit result can be one grey level off
  in a few pixels (up to ~0.25% of them in the tests).
- If the shrunken image would be a single pixel high or wide (an image no
  larger than the shrink factor along y or x, e.g. <= 4 px for a radius of
  31-100), ImageJ's enlarge step interpolates against an empty (zero) line and
  returns a corrupt background. This port skips shrinking for such images instead.
- ``shrink`` can override the automatic shrink factor; ImageJ has no such option.
- NaN or infinite pixels raise ``ValueError``; replace them first.

Provenance: ported line by line from ``BackgroundSubtracter.java`` in
https://github.com/imagej/ImageJ (public domain). ImageJ's rolling ball is
based on the NIH Image Pascal version by Michael Castle and Janice Keller,
after S. R. Sternberg, "Biomedical Image Processing", IEEE Computer, January
1983. The Java method each function ports is named in its docstring.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi

__all__ = [
    "rolling_ball_background",
    "subtract_background",
    "subtract_precomputed",
    "imagej_shrink_factor",
    "rolling_ball_patch",
]

# ImageJ's RollingBall constructor: (largest radius, shrink factor, arc-trim percent)
_IMAGEJ_BALL_TABLE = (
    (10, 1, 24),
    (30, 2, 24),
    (100, 4, 32),
    (np.inf, 8, 40),
)

# ImageJ's integer image types and their largest value; any other dtype is treated as 32-bit float
_IMAGEJ_INT_MAX = {np.dtype(np.uint8): 255.0, np.dtype(np.uint16): 65535.0}


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def rolling_ball_background(
    image: np.ndarray,
    radius: float,
    *,
    light_background: bool = False,
    presmooth: bool = True,
    shrink: int | None = None,
) -> np.ndarray:
    """Estimate the background the way ImageJ's rolling ball does.

    Equivalent to ImageJ's Subtract Background with "Create background (don't
    subtract)" ticked, except that the result is always float64.

    Parameters
    ----------
    image : array_like, shape (..., y, x)
        Any real dtype. Every 2D plane over the last two axes is processed
        independently, as ImageJ does for stacks and hyperstacks, so the last
        two axes must be y and x (not channels-last).
    radius : float
        Ball radius in pixels, as in ImageJ's dialog (default there: 50). Use
        a value at least as large as the radius of the largest object that is
        not part of the background.
    light_background : bool
        Bright background with dark objects (ImageJ "Light background"). The
        image is negated, rolled, and negated back, so the ball runs along the
        top of the intensity surface.
    presmooth : bool
        Apply ImageJ's 3x3 mean before estimating the background (``False`` =
        "Disable smoothing" ticked). Smoothing removes noise from the estimate,
        so the background is no longer guaranteed to lie below every raw pixel.
    shrink : int, optional
        Override ImageJ's automatic shrink factor (1 for radius <= 10, 2 for
        <= 30, 4 for <= 100, else 8). ``1`` turns shrinking off; the result is
        then closer to an ideal rolling ball but much slower for large radii.

    Returns
    -------
    background : ndarray of float64, same shape as ``image``.
    """
    image = np.asarray(image)
    if radius is None or not np.isfinite(radius) or radius <= 0:
        raise ValueError(f"radius must be a positive number of pixels, got {radius!r}")
    if image.ndim < 2:
        raise ValueError(f"image must have at least 2 dimensions (..., y, x), got shape {image.shape}")
    factor = imagej_shrink_factor(radius) if shrink is None else int(shrink)
    if factor < 1:
        raise ValueError(f"shrink must be >= 1, got {shrink!r}")

    sign = -1.0 if light_background else 1.0
    work = sign * image.astype(np.float64)
    if not np.all(np.isfinite(work)):
        raise ValueError("image contains NaN or infinite values; replace them before estimating the background")
    planes = work.reshape(-1, *image.shape[-2:])
    bg = np.stack([_rolling_ball_2d(p, radius, presmooth=presmooth, shrink_factor=factor) for p in planes])
    return sign * bg.reshape(image.shape)


def subtract_background(
    image: np.ndarray,
    radius: float,
    *,
    light_background: bool = False,
    presmooth: bool = True,
    shrink: int | None = None,
) -> np.ndarray:
    """ImageJ's *Process > Subtract Background...* (rolling ball): the corrected image.

    The output dtype and clipping follow ImageJ's rules for the input type;
    see `subtract_precomputed`. Arguments are as for `rolling_ball_background`.
    """
    background = rolling_ball_background(
        image, radius, light_background=light_background, presmooth=presmooth, shrink=shrink
    )
    return subtract_precomputed(image, background, light_background=light_background)


def subtract_precomputed(image: np.ndarray, background: np.ndarray, *, light_background: bool = False) -> np.ndarray:
    """Subtract a background from an image the way ImageJ writes the result back.

    ImageJ ``BackgroundSubtracter.rollingBallBackground``, subtraction step.
    Useful when the background was estimated once and is applied to several
    images, or was computed with `rolling_ball_background` for inspection.

    - ``uint8`` / ``uint16`` (ImageJ 8-/16-bit): ``image - background``,
      rounded, clipped to 0..255 / 0..65535, and returned in the input dtype.
      With ``light_background`` the type's maximum is added first, so the
      background becomes white rather than black.
    - Any other dtype (ImageJ 32-bit): ``image - background`` as float64, with
      no offset and no clipping. Pixels below the background stay negative.

    ``light_background`` must match the value used to estimate ``background``.
    """
    image = np.asarray(image)
    background = np.asarray(background, dtype=np.float64)
    if background.shape != image.shape:
        raise ValueError(f"background shape {background.shape} does not match image shape {image.shape}")
    diff = image.astype(np.float64) - background
    top = _IMAGEJ_INT_MAX.get(image.dtype)
    if top is None:
        return diff
    offset = top + 0.5 if light_background else 0.5  # the 0.5 rounds, as in ImageJ
    return np.floor(np.clip(diff + offset, 0.0, top)).astype(image.dtype)


def imagej_shrink_factor(radius: float) -> int:
    """Shrink factor ImageJ uses for ``radius``: 1 (<= 10), 2 (<= 30), 4 (<= 100), else 8."""
    return _ball_table_row(radius)[0]


def rolling_ball_patch(radius: float, shrink_factor: int) -> np.ndarray:
    """The ball as a height map: ImageJ ``RollingBall.buildRollingBall``.

    A square patch from the top of a sphere of radius ``r_small = radius /
    shrink_factor`` (at least 1), holding ``sqrt(r_small^2 - x^2 - y^2)`` on a
    ``(2 * half_width + 1)``-wide grid. ``half_width`` is ``r_small`` minus a
    trim of 24-40% (the percentage depends on the requested ``radius``, as in
    ImageJ). Grid points outside the sphere get height 0, also as in ImageJ.
    """
    trim_per = _ball_table_row(radius)[1]
    small_r = max(radius / shrink_factor, 1.0)
    xtrim = int(trim_per * small_r) // 100
    half_width = int(np.floor(small_r - xtrim + 0.5))  # Java Math.round rounds halves up; Python's round() does not
    yy, xx = np.mgrid[-half_width : half_width + 1, -half_width : half_width + 1]
    temp = small_r * small_r - xx * xx - yy * yy
    return np.where(temp > 0, np.sqrt(np.clip(temp, 0, None)), 0.0)


# --------------------------------------------------------------------------
# ImageJ steps (BackgroundSubtracter.java)
# --------------------------------------------------------------------------


def _ball_table_row(radius: float) -> tuple[int, int]:
    """(shrink factor, arc-trim percent) from ImageJ's ``RollingBall`` constructor."""
    for max_r, shrink, trim in _IMAGEJ_BALL_TABLE:
        if radius <= max_r:
            return shrink, trim
    raise AssertionError("unreachable")


def _rolling_ball_2d(plane: np.ndarray, radius: float, *, presmooth: bool, shrink_factor: int) -> np.ndarray:
    """ImageJ ``rollingBallFloatBackground`` for one float64 plane (already negated for a light background)."""
    if presmooth:
        plane = _presmooth_3x3(plane)
    h, w = plane.shape
    # ImageJ's enlarge step needs a shrunken image at least 2 px each way; below that
    # it interpolates against a zero line and returns a corrupt background, so skip shrinking.
    if shrink_factor > 1 and (-(-h // shrink_factor) < 2 or -(-w // shrink_factor) < 2):
        shrink_factor = 1
    patch = rolling_ball_patch(radius, shrink_factor)
    if shrink_factor == 1:
        return _roll_ball(plane, patch)
    small_bg = _roll_ball(_shrink_min(plane, shrink_factor), patch)
    return _enlarge(small_bg, (h, w), shrink_factor)


def _presmooth_3x3(image: np.ndarray) -> np.ndarray:
    """ImageJ ``filter3x3(fp, MEAN)``: 3-point mean along x, then along y; edge pixels repeated."""
    out = ndi.uniform_filter1d(image, size=3, axis=1, mode="nearest")
    return ndi.uniform_filter1d(out, size=3, axis=0, mode="nearest")


def _shrink_min(image: np.ndarray, factor: int) -> np.ndarray:
    """ImageJ ``shrinkImage``: each small pixel is the min of its ``factor x factor`` block (ragged at the far edges)."""
    h, w = image.shape
    sh, sw = -(-h // factor), -(-w // factor)
    padded = np.full((sh * factor, sw * factor), np.inf, dtype=np.float64)
    padded[:h, :w] = image
    return padded.reshape(sh, factor, sw, factor).min(axis=(1, 3))


def _roll_ball(image: np.ndarray, patch: np.ndarray) -> np.ndarray:
    """ImageJ ``rollBall``: grayscale opening with the non-flat ``patch``.

    The ball centre also visits positions up to the patch half-width outside
    the image, and only points inside the image constrain or receive the ball,
    which is how ImageJ clips the patch at the border. Padding with +inf
    for the erosion and -inf for the dilation does the same.
    """
    pad = patch.shape[0] // 2
    padded = np.pad(image, pad, mode="constant", constant_values=np.inf)
    eroded = ndi.grey_erosion(padded, structure=patch, mode="constant", cval=np.inf)
    dilated = ndi.grey_dilation(eroded, structure=patch, mode="constant", cval=-np.inf)
    return dilated[pad:-pad, pad:-pad]


def _interp_arrays(length: int, small_length: int, factor: int) -> tuple[np.ndarray, np.ndarray]:
    """ImageJ ``makeInterpolationArrays``: lower small-image index and its weight for each full-size pixel."""
    i = np.arange(length)
    small_index = (i - factor // 2) // factor
    small_index = np.clip(small_index, 0, small_length - 2)  # Java's truncating division gives 0 at the low edge
    distance = (i + 0.5) / factor - (small_index + 0.5)
    return small_index, 1.0 - distance


def _enlarge(small: np.ndarray, shape: tuple[int, int], factor: int) -> np.ndarray:
    """ImageJ ``enlargeImage``: bilinear interpolation between small-pixel centres, extrapolating at the edges."""
    h, w = shape
    sh, sw = small.shape
    yi, wy = _interp_arrays(h, sh, factor)
    xi, wx = _interp_arrays(w, sw, factor)
    row0 = small[yi, :]
    row1 = small[yi + 1, :]
    lines0 = row0[:, xi] * wx + row0[:, xi + 1] * (1.0 - wx)
    lines1 = row1[:, xi] * wx + row1[:, xi + 1] * (1.0 - wx)
    return lines0 * wy[:, None] + lines1 * (1.0 - wy[:, None])
