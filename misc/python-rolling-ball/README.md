# ImageJ rolling-ball background subtraction in Python

`imagej_rolling_ball.py` is a single-file Python port of ImageJ's
**Process > Subtract Background...** (rolling-ball mode). It needs only numpy
and scipy, and it gives the same result as ImageJ, so a Python pipeline can
reproduce a background subtraction done in Fiji. It has been checked against
ImageJ 1.54p itself (see [How close is it to ImageJ?](#how-close-is-it-to-imagej)).

## Get it

Copy the one file into your project. Nothing else in this folder is needed to
use it.

```bash
curl -O https://raw.githubusercontent.com/JeffLeeLab/Image-analysis/main/misc/python-rolling-ball/imagej_rolling_ball.py
uv add numpy scipy        # or: pip install numpy scipy
```

For reproducible analyses, replace `main` in the URL with a commit hash so the
copy cannot change under you. Requires Python 3.9 or later; tested with
Python 3.9 + numpy 1.24 + scipy 1.10 and Python 3.13 + numpy 2.4 + scipy 1.18.

## Quick start

```python
import numpy as np
import tifffile
from imagej_rolling_ball import rolling_ball_background, subtract_background

img = tifffile.imread("cells.tif")             # (y, x), (z, y, x), (c, z, y, x), ... any dtype

out = subtract_background(img, radius=50)      # what ImageJ gives: same dtype, clipped like ImageJ
bg = rolling_ball_background(img, radius=50)   # the background itself (float64)
raw = subtract_background(img.astype(np.float32), radius=50)  # float result, negatives kept
```

The last two axes must be y and x. Every 2D plane is processed on its own, as
ImageJ does for stacks and hyperstacks, so a channels-last array such as
`(y, x, c)` must be moved to `(c, y, x)` first.

## Functions

| Function | What it does |
|---|---|
| `subtract_background(image, radius, ...)` | The whole ImageJ command: estimate the background and subtract it. |
| `rolling_ball_background(image, radius, ...)` | The background only, as float64 (ImageJ's "Create background (don't subtract)"). |
| `subtract_precomputed(image, background, light_background=False)` | The subtraction step alone, for a background you already have. |
| `imagej_shrink_factor(radius)` | The shrink factor ImageJ uses for this radius (1, 2, 4 or 8). |
| `rolling_ball_patch(radius, shrink_factor)` | The ball as a height map, as ImageJ builds it. |

Keyword arguments of the first two:

| ImageJ dialog | Argument | Default |
|---|---|---|
| Rolling ball radius | `radius` (pixels) | required (ImageJ suggests 50) |
| Light background | `light_background=True` | `False` |
| Disable smoothing | `presmooth=False` | `True` (smoothing on, as in ImageJ) |
| Create background (don't subtract) | call `rolling_ball_background` | |
| — (no ImageJ equivalent) | `shrink=` overrides the automatic shrink factor; `1` = no shrinking | automatic |
| Separate colors, Sliding paraboloid, Correct corners | not ported | |

### Output type of the subtraction

The result follows ImageJ's rules for the input type:

| Input dtype | ImageJ type | Result |
|---|---|---|
| `uint8`, `uint16` | 8-bit, 16-bit | `image - background`, rounded and clipped to 0–255 / 0–65535, same dtype. With `light_background=True`, 255 / 65535 is added first so the background becomes white. |
| anything else (`float32`, `float64`, `int32`, ...) | 32-bit | `image - background` as float64. No offset, no clipping: pixels below the background stay negative. |

With presmoothing on (the default), the background is estimated from a
smoothed copy but subtracted from the raw image. Noise therefore dips a little
below the background, which ImageJ clips to 0 for 8-/16-bit images and keeps
negative for 32-bit ones. Convert to float first if negatives matter, for
example when averaging intensities in background regions.

## Choosing the radius

ImageJ's advice is that the radius should be at least as large as the radius
of the largest object in the image that is not part of the background. A
radius that is too small lets the ball roll into objects and subtracts part of
their signal. A radius that is too large leaves uneven illumination behind.

To keep large radii fast, ImageJ shrinks the image before rolling the ball,
then enlarges the result:

| Radius (px) | Shrink factor | 2048 × 2048 plane |
|---|---|---|
| ≤ 10 | 1 (no shrinking) | ~5 s at radius 10 |
| ≤ 30 | 2 | |
| ≤ 100 | 4 | ~0.5 s at radius 50 |
| > 100 | 8 | ~0.3 s at radius 150 |

Timings are for an Apple M1 Pro. `shrink=1` at radius 50 takes about
90 s on the same plane, so turn shrinking off only when you need an
unshrunk ball.

## How it works

Treat the image as a landscape whose height is the pixel intensity. A ball
of the given radius is rolled along the underside of that surface. At each
pixel, the background is the highest point the ball's surface reaches there
while the ball stays below the landscape: a grayscale *opening* with a
ball-shaped structuring element. Peaks narrower than the ball (cells, spots)
are too narrow for the ball to enter, so they stay above the background.

ImageJ's implementation, reproduced step by step here:

1. **Presmooth** (optional): 3 × 3 mean, so single noisy pixels do not pull
   the background down.
2. **Shrink**: replace each `f × f` block by its minimum, with `f` from the
   table above.
3. **Roll the ball**: an opening with a "ball patch", the top of a sphere of
   radius `radius / f`, trimmed to a square 24–40 % narrower than the sphere.
4. **Enlarge**: bilinear interpolation back to the original size.

Each function in the file names the Java method it ports, so the code can be
read side by side with
[`BackgroundSubtracter.java`](https://github.com/imagej/ImageJ/blob/master/ij/plugin/filter/BackgroundSubtracter.java).

## How close is it to ImageJ?

The port was compared with ImageJ 1.54p, called directly through its Java API
(`BackgroundSubtracter.rollingBallBackground`), on synthetic images with a
tilted, wavy background, blobs of radius 2–25 px and noise:

- **Background:** agrees to float32 rounding (largest difference about 1e-6
  of the image range) for radii 1–250 px, with smoothing on and off, light and
  dark backgrounds, and image sizes from 1 × 50 up to 213 × 301 px.
- **8-/16-bit subtracted images:** within one grey level everywhere; at most
  0.25 % of pixels differ by that one level. The cause is that ImageJ works in
  float32 and this port in float64.
- **32-bit subtracted images:** agree to float32 rounding.

`tests/` keeps a 19-case subset of that comparison as a fixture, so the check
runs without Java:

```bash
uv run --no-project --with numpy --with scipy --with pytest pytest misc/python-rolling-ball/tests
```

`tests/make_imagej_reference.py` rebuilds the fixture. It downloads the ImageJ
jar from Maven Central, checks its SHA-1, and runs
`tests/RunBackgroundSubtracter.java`. It needs a Java JDK.

## Differences from ImageJ

- **Precision:** float64 instead of float32, as described above.
- **Very small images:** if the shrunken image would be only one pixel high or
  wide (for example an image at most 4 px tall with a radius of 31–100),
  ImageJ's enlarge step interpolates against an empty line and returns a wrong
  background. This port skips shrinking for such images instead.
- **`shrink`:** can override the shrink factor; ImageJ has no such option.
- **NaN / inf pixels:** raise an error; replace them first.
- **Not ported:** RGB images, sliding paraboloid, and the "Correct corners"
  option (which only applies to the paraboloid).

## Why not `skimage.restoration.rolling_ball`?

scikit-image's `rolling_ball` gives a different and systematically lower
background. For each pixel it returns how high the top of the ball can sit
when centred on that pixel (an *erosion*), not the surface reached by the
ball in all its positions (the *opening* ImageJ computes). It also has no
presmoothing or shrinking. On the test images (intensity range ~1000) its
background was below ImageJ's at every pixel, by a median of 34–45 grey
levels at radii 10 and 50. Use this port when results need to match ImageJ.

## Copies in other projects

This folder holds the original. Projects vendor the code so they stay self-contained, and every copy points back here. To change the algorithm, edit it here and run the tests, then copy it into each place below.

| Copy | What is copied | How to check it is in sync |
|---|---|---|
| `people/AlfredoCastello/Zhiling-Marko-HIV/src/imagej_rolling_ball.py` | The whole file, with a project-specific `subtract_background_image` appended at the end. | The command below |
| `omero/server-side-script/subtract-background/Rolling_Ball_Background.py` | The rolling-ball functions, inline between `BEGIN`/`END copied from` markers, because OMERO server-side scripts must be a single file. After a re-copy, bump `VERSION` and re-upload the script to OMERO. | `test_local.py` (`AlgorithmIsCopied`) |
| RDI-calculator `rdi/background.py` (a separate repository) | The rolling-ball functions. RDI keeps its own subtraction and empty-area correction. | Its `tests/test_background.py` parity test, against a copy of `tests/imagej_reference.npz` |

Check the HIV copy from the repository root. It prints `in sync` when the file starts with this one, byte for byte:

```bash
f=misc/python-rolling-ball/imagej_rolling_ball.py
head -c "$(wc -c < $f)" people/AlfredoCastello/Zhiling-Marko-HIV/src/imagej_rolling_ball.py | cmp -s - $f && echo in sync
```

## History

Earlier versions of this port, in RDI-calculator, the HIV project and the OMERO script, had three problems. They were found by comparing with ImageJ 1.54p and fixed here, and all three copies were updated on 2026-10-26.

1. **Ball width rounding.** Java's `Math.round` rounds halves up and Python's `round` rounds them to even, so the ball patch was 2 px too narrow whenever `radius / shrink - trim` ended in .5. That changed the background at radii 7.5, 11, 15, 17, 21, 27, 34, 38, 46, **50** (ImageJ's default), 58, 70, 82, 94, 108, 132, 140, ... by up to ~7 % of the image range. Other radii, including 10 and 100, were unaffected.
2. **Small-image check.** The "too small to shrink" test used floor division where ImageJ uses ceiling. Some small images that ImageJ shrinks (e.g. 6 × 40 px at radius 50) were therefore processed unshrunk.
3. **Subtraction.** The old `subtract_background_image` clipped every result at 0 and returned float64. With a light background that gave an all-zero image. `subtract_background` / `subtract_precomputed` now follow ImageJ's per-type rules.

This version also handles any number of leading axes (not just 2D/3D), rejects NaN, and smooths along x then y in ImageJ's order.

## Files

```
imagej_rolling_ball.py              the module (the only file you need)
tests/test_imagej_rolling_ball.py   unit tests + parity with ImageJ
tests/imagej_reference.npz          ImageJ 1.54p inputs and outputs (19 cases)
tests/make_imagej_reference.py      regenerates the .npz (needs Java)
tests/RunBackgroundSubtracter.java  runs ImageJ's BackgroundSubtracter for the script above
```

## Credits

ImageJ 1.x, including `BackgroundSubtracter.java`, is in the public domain
(Wayne Rasband, NIH). ImageJ's rolling ball is based on the NIH Image Pascal
version by Michael Castle and Janice Keller, after S. R. Sternberg,
"Biomedical Image Processing", *IEEE Computer* 16(1), 1983.
