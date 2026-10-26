"""Tests for imagej_rolling_ball.py: building blocks, behaviour, and parity with ImageJ.

    uv run --no-project --with numpy --with scipy --with pytest pytest misc/python-rolling-ball/tests

The parity tests compare against imagej_reference.npz, made by running ImageJ
itself (see make_imagej_reference.py); they need no Java.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage as ndi

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import imagej_rolling_ball as rb  # noqa: E402

REFERENCE = Path(__file__).resolve().parent / "imagej_reference.npz"


def _scene(shape=(120, 140), seed=0, noise=3.0):
    """Two bright cells (400 above background) on a linear-gradient background with a flat offset.

    Returns (image, cell_mask).
    """
    rng = np.random.default_rng(seed)
    h, w = shape
    yy, xx = np.mgrid[:h, :w]
    cells = np.zeros(shape, dtype=bool)
    cells[h // 5 : h // 2, w // 6 : w // 2] = True
    cells[h // 2 + 5 : h - 10, w // 2 + 10 : w - 10] = True
    image = 150.0 + 0.4 * (xx + 0.5 * yy) + 400.0 * cells + rng.normal(0, noise, shape)
    return image, cells


# --------------------------------------------------------------------------
# parity with ImageJ
# --------------------------------------------------------------------------


def _reference_cases():
    with np.load(REFERENCE) as ref:
        meta = json.loads(str(ref["meta"]))
    return [pytest.param(c, id=c["output"]) for c in meta["cases"]]


@pytest.fixture(scope="module")
def reference():
    with np.load(REFERENCE) as ref:
        return {k: ref[k] for k in ref.files}


@pytest.mark.parametrize("case", _reference_cases())
def test_matches_imagej(reference, case):
    image = reference[case["input"]]
    expected = reference[case["output"]]
    kwargs = dict(light_background=case["light_background"], presmooth=case["presmooth"])
    if case["create_background"]:
        got = rb.rolling_ball_background(image, case["radius"], **kwargs)
    else:
        got = rb.subtract_background(image, case["radius"], **kwargs)
    if np.issubdtype(image.dtype, np.integer):
        # ImageJ rounds float32 values; float64 here puts a rare pixel one grey level away
        assert got.dtype == image.dtype
        diff = np.abs(got.astype(np.int64) - expected.astype(np.int64))
        assert diff.max() <= 1
        assert np.mean(diff > 0) < 0.005
    else:
        # float32 (ImageJ) vs float64 (here) arithmetic
        assert np.max(np.abs(got - expected)) <= 1e-5 * np.ptp(image)


# --------------------------------------------------------------------------
# building blocks
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "radius, shrink", [(5, 1), (10, 1), (10.5, 2), (30, 2), (31, 4), (100, 4), (100.1, 8), (150, 8), (500, 8)]
)
def test_shrink_table(radius, shrink):
    assert rb.imagej_shrink_factor(radius) == shrink


def test_patch_geometry():
    # radius 150, shrink 8 -> r_small 18.75, trim 40% -> xtrim 7, half width 12, width 25
    patch = rb.rolling_ball_patch(150, 8)
    assert patch.shape == (25, 25)
    assert patch[12, 12] == pytest.approx(18.75)
    assert patch.min() > 0.0  # the 40% trim keeps the whole square inside the sphere
    # radius 5, shrink 1 -> xtrim 1, half width 4; corners (5.66 px out) lie outside the ball -> 0
    small = rb.rolling_ball_patch(5, 1)
    assert small.shape == (9, 9)
    assert small[0, 0] == 0.0 and small[4, 4] == pytest.approx(5.0)


@pytest.mark.parametrize("radius, width", [(50, 19), (7.5, 15), (15, 15), (25, 21)])
def test_patch_rounds_half_up_like_java(radius, width):
    # 50 -> r_small 12.5, xtrim 4, 8.5 -> 9 (Python's round() would give 8); 25 -> 9.5 -> 10 either way
    assert rb.rolling_ball_patch(radius, rb.imagej_shrink_factor(radius)).shape == (width, width)


def test_shrink_min_is_block_minimum_with_ragged_edge():
    img = np.arange(7 * 9, dtype=float).reshape(7, 9)
    small = rb._shrink_min(img, 4)
    assert small.shape == (2, 3)
    assert small[0, 0] == 0.0
    assert small[1, 2] == img[4:, 8:].min()


def test_enlarge_is_exact_for_a_plane():
    # bilinear interpolation reproduces a linear function, including ImageJ's edge extrapolation
    h, w, f = 37, 53, 4
    yy, xx = np.mgrid[:h, :w]
    full = 2.0 + 0.5 * xx - 0.25 * yy
    small_y = (np.arange(-(-h // f)) + 0.5) * f - 0.5  # small-pixel centres in full-size coordinates
    small_x = (np.arange(-(-w // f)) + 0.5) * f - 0.5
    small = 2.0 + 0.5 * small_x[None, :] - 0.25 * small_y[:, None]
    assert np.allclose(rb._enlarge(small, (h, w), f), full)


def test_roll_ball_is_an_opening_below_the_image():
    img = np.random.default_rng(3).uniform(0, 100, (30, 40))
    patch = rb.rolling_ball_patch(6, 1)
    bg = rb._roll_ball(img, patch)
    assert np.all(bg <= img + 1e-9)
    # away from the border it is scipy's grey opening with the same structure
    pad = patch.shape[0] // 2
    inner = (slice(2 * pad, -2 * pad), slice(2 * pad, -2 * pad))
    assert np.allclose(bg[inner], ndi.grey_opening(img, structure=patch)[inner])


# --------------------------------------------------------------------------
# behaviour
# --------------------------------------------------------------------------


def test_constant_image():
    img = np.full((50, 60), 37, dtype=np.uint16)
    assert np.allclose(rb.rolling_ball_background(img, 20), 37.0)
    out = rb.subtract_background(img, 20)
    assert out.dtype == np.uint16 and np.all(out == 0)
    assert np.all(rb.subtract_background(img, 20, light_background=True) == 65535)


def test_removes_gradient_and_keeps_cells():
    image, cells = _scene()
    out = rb.subtract_background(image, 60)  # radius larger than the cells
    assert abs(np.median(out[~cells])) < 10.0
    assert np.median(out[cells]) == pytest.approx(400.0, abs=15.0)


def test_small_radius_eats_large_objects():
    # the failure mode the radius guards against
    image, _ = _scene()
    out = rb.subtract_background(image, 5)
    assert np.median(out) < 20.0


def test_stack_and_hyperstack_equal_per_plane():
    image, _ = _scene((60, 70))
    stack = np.stack([image, 0.5 * image, image + 20])
    hyper = np.stack([stack, stack[::-1]])  # (c, z, y, x)
    bg = rb.rolling_ball_background(hyper, 25)
    assert bg.shape == hyper.shape
    for c in range(2):
        for z in range(3):
            assert np.array_equal(bg[c, z], rb.rolling_ball_background(hyper[c, z], 25))


def test_light_background_mirrors_dark():
    image, _ = _scene()
    bg_dark = rb.rolling_ball_background(image, 60)
    bg_light = rb.rolling_ball_background(1000.0 - image, 60, light_background=True)
    assert np.allclose(bg_light, 1000.0 - bg_dark)


def test_unsmoothed_unshrunk_background_never_exceeds_image():
    # a strict lower envelope; presmoothing or the bilinear enlarge can overshoot a few pixels
    image, _ = _scene((100, 120))
    assert np.all(rb.rolling_ball_background(image, 40, presmooth=False, shrink=1) <= image + 1e-9)


def test_shrink_override_approximates_unshrunk():
    image, _ = _scene((100, 120))
    auto = rb.rolling_ball_background(image, 60)  # shrink 4
    full = rb.rolling_ball_background(image, 60, shrink=1)
    assert np.median(np.abs(auto - full)) < 5.0


def test_subtract_output_types():
    image, _ = _scene((60, 70))
    u16 = rb.subtract_background(image.astype(np.uint16), 30)
    u8 = rb.subtract_background(np.clip(image / 4, 0, 255).astype(np.uint8), 30)
    f32 = rb.subtract_background(image.astype(np.float32), 30)
    i32 = rb.subtract_background(image.astype(np.int32), 30)
    assert u16.dtype == np.uint16 and u8.dtype == np.uint8
    assert f32.dtype == np.float64 and i32.dtype == np.float64
    assert f32.min() < 0  # 32-bit keeps pixels below the (smoothed) background negative


def test_subtract_precomputed_matches_subtract_background():
    image = _scene((60, 70))[0].astype(np.uint16)
    bg = rb.rolling_ball_background(image, 30, light_background=True)
    assert np.array_equal(
        rb.subtract_precomputed(image, bg, light_background=True),
        rb.subtract_background(image, 30, light_background=True),
    )


def test_bad_inputs():
    img = np.zeros((10, 10))
    for radius in (0, -1, np.nan, None):
        with pytest.raises(ValueError):
            rb.rolling_ball_background(img, radius)
    with pytest.raises(ValueError):
        rb.rolling_ball_background(np.zeros(10), 5)
    with pytest.raises(ValueError):
        rb.rolling_ball_background(img, 5, shrink=0)
    with pytest.raises(ValueError):
        rb.rolling_ball_background(np.where(np.eye(10) > 0, np.nan, 1.0), 5)
    with pytest.raises(ValueError):
        rb.subtract_precomputed(img, np.zeros((10, 11)))
