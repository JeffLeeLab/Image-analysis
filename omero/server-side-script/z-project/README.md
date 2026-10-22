# Z-projection (OMERO script)

[Z_Projection.py](Z_Projection.py) is a single-file OMERO script. It projects each image along Z using maximum, sum or average intensity, for every channel and timepoint. By default it uses every Z-plane; you can optionally give a Z range instead. It writes the results as new images into one dataset and never changes the originals. It needs only NumPy, which every OMERO server already has.

**Status:** the local checks (`test_local.py`) pass with the server's versions of Python (3.11) and NumPy (2.4.6). The script has **not yet been run on the OMERO server**; see [Live check](#live-check).

## Using it in OMERO.web

1. Select images, or one dataset, then choose **Scripts → Z_Projection**.
2. Choose a `Projection`. The default is `Max intensity`.
3. Optionally set `Z_Start` and/or `Z_End` to project only some Z-planes. Leave both blank (the default) to project the whole stack. See [Z range](#z-range).
4. Choose where results go: **either** `Output_Dataset_ID` (an existing dataset) **or** `New_Dataset_Name`.
5. Optionally tick `Dry_Run` (off by default). The message then lists every planned output name and pixel type, and nothing is written.
6. Run.

Rules:

- **Pixel types:** the inputs can be uint8, uint16 or float32. See [Outputs](#outputs) for the output types.
- **Single Z-plane:** images with only one Z-plane are skipped and listed in the message. The rest of the batch runs.
- **Mixed images:** images in one run may have different sizes and channels.
- **Image size:** very large images that OMERO stores as pyramids are rejected, because the script reads whole planes.
- **Checks first:** everything is checked before anything is written.
- **Failures:** if one image fails during processing, its partial output is deleted and the rest continue. The message lists the failures.
- **`Name_Filter`** is an optional wildcard on the full image name, for example `*.tif`. Earlier `_mip`/`_sum`/`_avg` results have one Z-plane, so they are skipped anyway.
- **Cancelling** a running job can leave the image being written as a partial image under *Orphaned Images*. Delete it.

## Z range

`Z_Start` and `Z_End` are numbered from 1 and both ends are included, as in the OMERO.web viewer and FIJI. So 3–15 projects 13 planes.

| Z_Start | Z_End | Planes projected | Name |
|---|---|---|---|
| blank | blank | all (the default) | `{basename}_mip.tif` |
| 3 | 15 | 3 to 15 | `{basename}_mip3-15.tif` |
| 5 | blank | 5 to each image's last plane | `{basename}_mip5-30.tif` for a 30-plane image |
| blank | 10 | 1 to 10 | `{basename}_mip1-10.tif` |
| 7 | 7 | plane 7 only (a copy of it) | `{basename}_mip7-7.tif` |

The name always shows the planes used for that image. Images with different depths can therefore get different names in one run, as in the third row.

In a batch the same range applies to every image:
- **A range that does not fit any one image stops the whole run.** Nothing is written, and the message lists every image that does not fit. For example, `Z_End` 15 stops the run if one image has only 10 planes. Use `Dry_Run` to find these images first. Then narrow the range, leave `Z_End` blank to end at each image's last plane, or leave those images out with `Name_Filter` or your selection.
- **Single-Z images are still skipped**, not rejected. So a dataset that already holds earlier `_mip` results can be processed again.
- **`Z_Start` after `Z_End`** (for example 15 and 3) is an error, and nothing is written.

## Outputs

| Projection | Name | Output pixel type |
|---|---|---|
| Max intensity | `{basename}_mip.tif` | same as the source |
| Sum intensity | `{basename}_sum.tif` | uint8/uint16 → **uint32**; float32 → float32 |
| Average intensity | `{basename}_avg.tif` | always **float32** (as in ImageJ) |

Why these types:
- **OMERO pixel types:** OMERO supports int8, uint8, int16, uint16, int32, uint32, float, double, bit and complex. It has **no 64-bit integer type**.
- **Integer sums:**
  - They are calculated in uint64, then stored as uint32. That is exact, and it cannot overflow below 65,537 Z-planes of uint16.
  - float32 is not used for them, because it is exact only up to about 16.7 million. A uint16 stack of 256 or more bright planes can go past that.
- **Float sums and averages** are calculated in float64, then stored as float32.
- **Range check:** a result that does not fit its output type fails that image, and the message says so. A NaN or infinite value in a float input does the same.

Examples: `image_001.ome.tif` becomes `image_001_mip.tif`, `sample.tiff` becomes `sample_sum.tif`, and `sample.vsi` becomes `sample_avg.tif`. With a Z range of 3–15, `image_001.ome.tif` becomes `image_001_mip3-15.tif`.

About the names:
- **Only a known image extension is removed** to make `{basename}`: `.tif`, `.tiff`, `.ome.tif`, `.lif`, `.czi`, `.vsi`, `.nd2` and other common microscopy formats, in any case. Everything else is kept, including dots inside the name. So `..._63X.lif_-_P_10.tif` becomes `..._63X.lif_-_P_10_mip.tif`, and `cells_0.5uM` becomes `cells_0.5uM_mip.tif`. OMERO's names for series from multi-series files also stay distinct: `Experiment.lif [Position 10]` becomes `Experiment.lif [Position 10]_mip.tif`. The full list is `IMAGE_EXTENSIONS` in the script.
- `.tif` is only part of the OMERO image name. No TIFF file is created; export through OMERO as usual.
- A repeated run creates new images even when the names match. Use image IDs to tell them apart.

Each output also gets:
- **A description and a key-value annotation** (namespace `org.jeffleelab.omero.zproject`). It records:
  - the source image ID and name;
  - the projection;
  - the Z-planes used (`z_start` and `z_end`, numbered from 1, and `z_planes`, how many there were);
  - the source's Z size (`source_size_z`);
  - the source and output pixel types;
  - the script version and the time.
- **Copied metadata.** OMERO's `copyAndResizeImage` copies the source's X/Y/T sizes (Z = 1), physical pixel sizes and channel metadata. For Sum and Average, the script then changes the new image's pixel type before writing pixels. OMERO's own projection service does the same. The live check confirms both steps. Per-plane timing and positions, ROIs, tags, attachments and rendering settings are not copied.

The script does not use OMERO's built-in projection service. That service clamps sums to the source type's maximum, which loses data. It also leaves out the last Z-plane for sum and average.

## Installing (OMERO admin)

1. In OMERO.web, open **Scripts → Upload Script** and upload this script into the same folder as the others, such as `/jefflee_lab`. [Installing a script](../README.md#installing-a-script) has the details and the other ways to install.
2. Open **Z_Projection** from the Scripts menu and check that it shows the 9 fields.

To install a new version, upload the new file with the same name into the same folder. OMERO.web offers **Replace script**, and the script keeps its ID.

## Live check

Do this once after installing. Use a small multichannel uint16 z-stack and a test dataset.

1. **Dry run:** the output names, pixel types and destination are as expected.
   - With `Z_End` larger than the stack, the dry run lists the image as not fitting, and nothing is written.
   - Enter 0 in `Z_Start` and note whether OMERO.web stops you (the field minimum is 1). Otherwise the script rejects it, and nothing is written.
2. **Runs:** run once with each projection. Then run once with a Z range, for example `Z_Start` 2 and `Z_End` 3.
3. **In OMERO.web**, check that each output:
   - is in the chosen dataset, with the correct name;
   - has pixel type uint16 (max), uint32 (sum) or float (average);
   - has Z = 1 and the source's X/Y/C/T sizes, channel names and physical sizes;
   - has the key-value annotation, with `z_start`, `z_end` and `z_planes` matching the name (the whole stack when no range was given).

   The uint32 and float images must also render in the viewer. This confirms that changing the pixel type works.
4. **Pixels:** compare the outputs with a recalculation on your Mac. Run this in the `omero` conda env, from this folder:

```python
import getpass, importlib.util
import numpy as np
from omero.gateway import BlitzGateway

spec = importlib.util.spec_from_file_location("zp", "Z_Projection.py")
zp = importlib.util.module_from_spec(spec); spec.loader.exec_module(zp)
conn = BlitzGateway(input("User: "), getpass.getpass(), host="OMERO_HOST", port=4064, secure=True)
conn.connect()
src, out = conn.getObject("Image", SOURCE_ID), conn.getObject("Image", OUTPUT_ID)
method = "Sum intensity"  # the Projection used for this output, exactly as shown in the dialog
z_range = None  # None for the whole stack, or e.g. (2, 3) for an output named ..._sum2-3.tif
out_type = zp.output_pixel_type(zp.pixel_type(src), method)
ps, po = src.getPrimaryPixels(), out.getPrimaryPixels()
diffs = [np.abs(plane.astype(float) - po.getPlane(z, c, t))
         for z, c, t, plane in zp.output_planes(ps.getPlane, zp.z_indices(z_range, src.getSizeZ()),
                                                src.getSizeC(), src.getSizeT(), method, out_type)]
print("max difference:", max(d.max() for d in diffs))
conn.close()
```

   The difference must be 0 for every projection, including the Z-range output.

5. **In FIJI:** open one uint32 and one float output through the OMERO plugin or an OMERO export.
6. **As a normal user:** repeat one small run as a non-admin user in a normal group.

## Local tests (on your Mac)

```sh
conda activate omero
cd omero/server-side-script/z-project
python -m unittest test_local -v
```
