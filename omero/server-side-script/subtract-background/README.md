# Batch rolling-ball background subtraction (OMERO script)

[Batch_Rolling_Ball_Background.py](Batch_Rolling_Ball_Background.py) is a single-file OMERO script. It applies the lab's pinned ImageJ rolling-ball port to raw image planes, with one radius per channel. It writes the results as new images into one dataset and never changes the originals. The contract is in [spec.md](spec.md).

**Status:** the local checks (`test_local.py`) pass with the server's versions of Python (3.11), NumPy (2.4.6) and SciPy (1.17.1). The script has **not yet been run on the OMERO server**; see [Live check](#live-check).

## Using it in OMERO.web

1. Select images, or one dataset, then choose **Scripts → Batch_Rolling_Ball_Background**.
2. Enter a radius for each channel. `Channel_1_Radius` is the first channel, and radii are in XY pixels.
   - **0 keeps that channel unchanged. If every radius is 0, the image is copied.**
   - Leave the fields blank for channels the images don't have.
3. Choose where results go: **either** `Output_Dataset_ID` (an existing dataset) **or** `New_Dataset_Name`.
4. Run with `Dry_Run` ticked, which is the default. Check the message: it lists each channel's name with its radius, and every planned output name.
5. Untick `Dry_Run` and run again with the same settings.

Rules:

- **Matching channels:** every image in a run must have the same channels, with the same names in the same order, up to 6. X/Y/Z/T sizes may differ.
- **Pixel types:** uint8, uint16 and float32. Each output keeps its source's type.
- **Image size:** very large images that OMERO stores as pyramids are rejected, because the script reads whole planes.
- **Checks first:** everything is checked before anything is written.
- **Failures:** if one image fails during processing, its partial output is deleted and the rest continue. The message lists the failures.
- **`Name_Filter`** is an optional wildcard on the full image name, for example `*.tif`. If you select a dataset that already holds earlier `_bgs-` results, they are processed again unless the filter excludes them.
- **Cancelling** a running job can leave the image being written as a partial image under *Orphaned Images*. Delete it.

## Outputs

Names are fixed: `{basename}_bgs-{radius of channel 1}-{channel 2}-….tif`.

| Source name | Radii | Output name |
|---|---|---|
| `image_001.ome.tif` | 0, 50, 100 | `image_001_bgs-0-50-100.tif` |
| `sample.tiff` | 12.5, 0, 100 | `sample_bgs-12.5-0-100.tif` |
| `sample.vsi` | 0, 0, 0 | `sample_bgs-0-0-0.tif` |

About the names:
- `.tif` is only part of the OMERO image name. No TIFF file is created; export through OMERO as usual.
- A repeated run creates new images even when the names match. Use image IDs to tell them apart.

Each output also gets:
- **A description and a key-value annotation** (namespace `org.jeffleelab.omero.bgsub`). It records the source image ID and name, the radius for each channel, the settings, the algorithm commit and hash, the script version and the time.
- **Copied metadata.** OMERO's `copyAndResizeImage` copies the source's dimensions, pixel type, physical pixel sizes and channel metadata; the live check confirms which fields carry over. Per-plane timing and positions, ROIs, tags, attachments and rendering settings are not copied.

Fixed settings:
- presmoothing on;
- automatic shrink;
- dark background;
- negative corrected values clipped to 0;
- integer outputs rounded half up.

## Installing (OMERO admin)

The server needs no conda; everything runs in the OMERO server's own Python. SciPy 1.17.1 is already installed there (see [Server environment](../README.md#server-environment)).

1. In OMERO.web, open **Scripts → Upload Script** and upload `Env_Check.py`, then this script, into one folder such as `/jefflee_lab`. [Installing a script](../README.md#installing-a-script) has the details and the other ways to install.
2. Run **Env_Check** from the Scripts menu. Confirm that it reports SciPy 1.17.1 and NumPy 2.4.6, and note the `omero-py` version.
3. Open **Batch_Rolling_Ball_Background** from the Scripts menu and check that it shows the 12 fields.

To install a new version, upload the new file with the same name into the same folder. OMERO.web offers **Replace script**, and the script keeps its ID.

## Live check

Do this once after installing. Use a small multichannel image and a test dataset.

1. **Environment:** `Env_Check.py` reports SciPy 1.17.1 and NumPy 2.4.6.
2. **Dry run:** the channel names, radii, output name and destination are as expected.
3. **Runs:** run once with every radius 0, and once with real radii.
4. **Pixels:** compare the outputs with a recalculation on your Mac. Run this in the `omero` conda env, from this folder:

```python
import getpass, importlib.util
import numpy as np
from omero.gateway import BlitzGateway

spec = importlib.util.spec_from_file_location("bgs", "Batch_Rolling_Ball_Background.py")
bgs = importlib.util.module_from_spec(spec); spec.loader.exec_module(bgs)
conn = BlitzGateway(input("User: "), getpass.getpass(), host="OMERO_HOST", port=4064, secure=True)
conn.connect()
src, out = conn.getObject("Image", SOURCE_ID), conn.getObject("Image", OUTPUT_ID)
radii = [0, 50, 100]  # the radii used for this output
ps, po = src.getPrimaryPixels(), out.getPrimaryPixels()
diffs = [np.abs(bgs.correct_plane(ps.getPlane(z, c, t), radii[c]).astype(float) - po.getPlane(z, c, t))
         for z in range(src.getSizeZ()) for c in range(src.getSizeC()) for t in range(src.getSizeT())]
print("max difference:", max(d.max() for d in diffs))
conn.close()
```

   - With every radius 0, the difference must be 0.
   - With real radii, expect 0. A difference of 1 in integer images can come from different NumPy/SciPy builds on the server and your computer.
5. **In OMERO.web:** check that the output is in the chosen dataset, with the correct name, pixel type, sizes, channel names and physical sizes, and that it has the key-value annotation.
6. **As a normal user:** repeat one small run as a non-admin user in a normal group.

## Local tests (on your Mac)

```sh
conda activate omero      # includes SciPy; see ../../environment.yml
cd omero/server-side-script/subtract-background
python -m unittest test_local -v
```

## Algorithm provenance

- **Source:** the block between `BEGIN`/`END pinned imagej_rolling_ball.py` in the script is lines 21–206 of [imagej_rolling_ball.py](https://github.com/JeffLeeLab/Image-analysis/blob/e6360e0a7e04580ac30e143281edae66660f9ec6/people/AlfredoCastello/Zhiling-Marko-HIV/src/imagej_rolling_ball.py), copied verbatim. That is commit `e6360e0`, file SHA-256 `0713d5360395a9cf8748829b2f782d316fa3be57df1bc0827adff8a90af55c9c`.
- **Check:** `test_local.py` confirms the block still matches the pinned file.
- **Origin and validation:** the code comes from `rdi/background.py` in the owner's RDI-calculator project (commit `d30fae7`). It is a port of ImageJ's `BackgroundSubtracter.java`. Scientific validation was done there and is accepted, not repeated.
- **Licence:** no licence was found for the owner's source. Confirm redistribution rights before sharing it outside the institution.

The earlier multi-package version (v0.1.0, with a separate uv worker, external adapter and journal/resume) is kept in git commit `84108b4`.
