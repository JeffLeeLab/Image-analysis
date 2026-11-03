# Rolling-ball background subtraction (OMERO script)

[Rolling_Ball_Background.py](Rolling_Ball_Background.py) is a single-file OMERO script. It applies the lab's ImageJ rolling-ball port ([misc/python-rolling-ball](https://github.com/JeffLeeLab/Image-analysis/tree/main/misc/python-rolling-ball)) to raw image planes, with one radius per channel. It writes the results as new images into one dataset and never changes the originals. The contract is in [spec.md](spec.md).

**Status:** the local checks (`test_local.py`) pass with the server's versions of Python (3.11), NumPy (2.4.6) and SciPy (1.17.1). The script has **not yet been run on the OMERO server**; see [Live check](#live-check).

## Using it in OMERO.web

1. Select images, or one dataset, then choose **Scripts → Rolling_Ball_Background**.
2. Enter a radius for each channel. `Channel_1_Radius` is the first channel, and radii are in XY pixels.
   - **0 keeps that channel unchanged. If every radius is 0, the image is copied.**
   - Leave the fields blank for channels the images don't have.
3. Optionally choose a `Z_Projection` (default `Off`); see [Z projection](#z-projection).
4. Choose where results go: **either** `Output_Dataset_ID` (an existing dataset) **or** `New_Dataset_Name`.
5. Optionally tick `Dry_Run` (off by default) to check first. The message then lists each channel's name with its radius, the Z projection, and every planned output name, and nothing is written.
6. Run with `Dry_Run` unticked to process.

Rules:

- **Matching channels:** every image in a run must have the same channels, with the same names in the same order, up to 8. X/Y/Z/T sizes may differ.
- **Pixel types:** uint8, uint16 and float32. Each output keeps its source's type.
- **Image size:** very large images that OMERO stores as pyramids are rejected, because the script reads whole planes.
- **Checks first:** everything is checked before anything is written.
- **Failures:** if one image fails during processing, its partial output is deleted and the rest continue. The message lists the failures.
- **`Name_Filter`** is an optional wildcard on the full image name, for example `*.tif`. If you select a dataset that already holds earlier `_bgs-` results, they are processed again unless the filter excludes them.
- **Cancelling** a running job can leave the image being written as a partial image under *Orphaned Images*. Delete it.

### Z projection

`Z_Projection` makes a max-intensity projection along Z, so each output has a single Z-plane. It is `Off` by default.

- **Max projection, then subtract:** projects the raw slices, then estimates and removes one background from the projection. The rolling ball runs once per channel and timepoint, so this is about Z times faster.
- **Subtract, then max projection:** removes each slice's own background, then projects the corrected slices. This suits stacks whose background changes with depth.

The two orders give different pixels for any channel with a radius above 0. A channel with radius 0 is the raw max projection either way. An image with one Z-plane is left as it is.

## Outputs

Names are fixed: `{basename}_bgs-{radius of channel 1}-{channel 2}-….tif`. With a Z projection, `_mip` is placed where the projection happens in the processing order.

| Source name | Radii | Output name |
|---|---|---|
| `image_001.ome.tif` | 0, 50, 100 | `image_001_bgs-0-50-100.tif` |
| `sample.tiff` | 12.5, 0, 100 | `sample_bgs-12.5-0-100.tif` |
| `sample.vsi` | 0, 0, 0 | `sample_bgs-0-0-0.tif` |
| `image_001.ome.tif`, max projection then subtract | 0, 50 | `image_001_mip_bgs-0-50.tif` |
| `image_001.ome.tif`, subtract then max projection | 0, 50 | `image_001_bgs-0-50_mip.tif` |

About the names:
- **Only a known image extension is removed** to make `{basename}`: `.tif`, `.tiff`, `.ome.tif`, `.lif`, `.czi`, `.vsi`, `.nd2` and other common microscopy formats, in any case. Everything else is kept, including dots inside the name. So `..._63X.lif_-_P_10.tif` becomes `..._63X.lif_-_P_10_bgs-50.tif`, and `cells_0.5uM` becomes `cells_0.5uM_bgs-50.tif`. OMERO's names for series from multi-series files also stay distinct: `Experiment.lif [Position 10]` becomes `Experiment.lif [Position 10]_bgs-50.tif`. The full list is `IMAGE_EXTENSIONS` in the script.
- `.tif` is only part of the OMERO image name. No TIFF file is created; export through OMERO as usual.
- A repeated run creates new images even when the names match. Use image IDs to tell them apart.

Each output also gets:
- **A description and a key-value annotation** (namespace `org.jeffleelab.omero.bgsub`). It records the source image ID and name, the radius for each channel, the Z projection, the settings, the algorithm source, the script version and the time.
- **Copied metadata.** OMERO's `copyAndResizeImage` copies the source's dimensions (with Z = 1 for a projection), pixel type, physical pixel sizes and channel metadata; the live check confirms which fields carry over. Per-plane timing and positions, ROIs, tags, attachments and rendering settings are not copied.

Fixed settings:
- presmoothing on;
- automatic shrink;
- dark background;
- negative corrected values clipped to 0;
- integer outputs rounded half up.

## Performance

- **One reader per image.** Each image's original file is opened once, through one `RawPixelsStore`, and every plane is read from it. omero-py's `getPlane()` opens a new store, and so reopens the file through Bio-Formats, for every plane.
- **Fast decoding.** Planes are decoded with `np.frombuffer` instead of omero-py's `struct.unpack`, which is about 100 times slower.
- **Timing in stdout.** After each image, the job's stdout (shown in OMERO.web's activities panel) has a line such as `Image 3/12: 1234 cells.tif done in 14.2 s (read 1.1 s, write 0.6 s)`. Compute time is the rest. The `Message` output is unchanged.
- The size check during planning (including dry runs) still opens each image's file once, to ask OMERO whether the image is too large for whole-plane reads.

These changes affect speed only; the output pixels are meant to be identical to earlier versions. That, and the speed-up itself, has not yet been checked on the OMERO server.

## Installing (OMERO admin)

The server needs no conda; everything runs in the OMERO server's own Python. SciPy 1.17.1 is already installed there (see [Server environment](../README.md#server-environment)).

1. In OMERO.web, open **Scripts → Upload Script** and upload `Env_Check.py`, then this script, into one folder such as `/jefflee_lab`. [Installing a script](../README.md#installing-a-script) has the details and the other ways to install.
2. Run **Env_Check** from the Scripts menu. Confirm that it reports SciPy 1.17.1 and NumPy 2.4.6, and note the `omero-py` version.
3. Open **Rolling_Ball_Background** from the Scripts menu and check that it shows the 15 fields.

To install a new version, upload the new file with the same name into the same folder. OMERO.web offers **Replace script**, and the script keeps its ID.

## Live check

Do this once after installing. Use a small multichannel image and a test dataset.

1. **Environment:** `Env_Check.py` reports SciPy 1.17.1 and NumPy 2.4.6.
2. **Dry run:** the channel names, radii, output name and destination are as expected.
3. **Runs:** run once with every radius 0, once with real radii, and once with each Z projection order on a z-stack.
4. **Pixels:** compare the outputs with a recalculation on your Mac. Run this in the `omero` conda env, from this folder:

```python
import getpass, importlib.util
import numpy as np
from omero.gateway import BlitzGateway

spec = importlib.util.spec_from_file_location("bgs", "Rolling_Ball_Background.py")
bgs = importlib.util.module_from_spec(spec); spec.loader.exec_module(bgs)
conn = BlitzGateway(input("User: "), getpass.getpass(), host="OMERO_HOST", port=4064, secure=True)
conn.connect()
src, out = conn.getObject("Image", SOURCE_ID), conn.getObject("Image", OUTPUT_ID)
radii = [0, 50, 100]  # the radii used for this output
projection = "Off"    # the Z_Projection used for this output, exactly as shown in the dialog
ps, po = src.getPrimaryPixels(), out.getPrimaryPixels()
diffs = [np.abs(plane.astype(float) - po.getPlane(z, c, t))
         for z, c, t, plane in bgs.output_planes(ps.getPlane, src.getSizeZ(), src.getSizeC(),
                                                 src.getSizeT(), radii, projection)]
print("max difference:", max(d.max() for d in diffs))
conn.close()
```

   - With every radius 0, the difference must be 0.
   - With real radii, expect 0. A difference of 1 in integer images can come from different NumPy/SciPy builds on the server and your computer.
5. **In OMERO.web:** check that the output is in the chosen dataset, with the correct name, pixel type, sizes (Z = 1 for a projection), channel names and physical sizes, and that it has the key-value annotation.
6. **As a normal user:** repeat one small run as a non-admin user in a normal group.

## Local tests (on your Mac)

```sh
conda activate omero      # includes SciPy; see ../../environment.yml
cd omero/server-side-script/subtract-background
python -m unittest test_local -v
```

## Algorithm provenance

- **Original:** [`imagej_rolling_ball.py`](https://github.com/JeffLeeLab/Image-analysis/tree/main/misc/python-rolling-ball) in `misc/python-rolling-ball`, a port of ImageJ's `BackgroundSubtracter.java` that is checked numerically against ImageJ 1.54p. That is the one place to maintain it.
- **Copy here:** the block between `BEGIN`/`END copied from misc/python-rolling-ball/imagej_rolling_ball.py` in the script holds the functions the script uses, copied unchanged. OMERO server-side scripts must be a single file, so they are kept inline. Fix the original and copy the functions again; do not edit them in the script.
- **Check:** `test_local.py` confirms each copied function still matches the original, and that the background matches ImageJ's own output stored in `misc/python-rolling-ball/tests/imagej_reference.npz` (skipped if that folder is missing).
- **Licence:** confirm redistribution rights before sharing the code outside the institution.

## Changelog

- **0.5.0 (2026-11-03):** Faster pixel reading (see [Performance](#performance)): one reader per image instead of one per plane, `np.frombuffer` decoding, and per-image timing in stdout. Outputs are meant to be unchanged.
- **0.4.2 (2026-10-26):** ImageJ-parity fixes, taken from the original. (1) The rolling-ball patch width rounds halves up as Java does; before, radii such as 11, 15, 17, 21, 27, 34, 38, 46, 50, 58, 70, 82, 94, 108, 132, 140, 156, 164, 188, 212, 220, 236, 244, 268, 292 and 300 (and non-integers such as 7.5) used a patch 2 px too narrow, which gave backgrounds off by up to about 7% of the image range. Radii such as 10 and 100 were not affected. (2) Shrinking is skipped only when the shrunken image would be under 2 px (ceiling, not floor), as in ImageJ. (3) The 3x3 presmooth runs along x, then y. The algorithm source now points at `misc/python-rolling-ball`. The annotation key `algorithm_source` replaces `algorithm_commit` and `algorithm_sha256`.

The earlier multi-package version (v0.1.0, with a separate uv worker, external adapter and journal/resume) is kept in git commit `84108b4`.
