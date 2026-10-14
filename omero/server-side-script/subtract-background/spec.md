# OMERO batch rolling-ball background subtraction: specification v0.2

Revised 12 October 2026.

This revision replaces the v0.1 specification, which called for an uploaded entry script, an external adapter package, a separate uv worker environment and journal-based resume. That design and its implementation are kept in git commit `84108b4`.

Scientific validation was done in the owner's RDI-calculator project. It is accepted for the unchanged pinned algorithm and is not repeated here.

## 1. Contract

| Area | Requirement |
|---|---|
| Delivery | One self-contained script, `Batch_Rolling_Ball_Background.py`, installed as an official (trusted) OMERO script in the server's `lib/scripts/` folder, through OMERO.web's Upload Script, a file copy or the CLI. No helper modules. |
| Runtime | The OMERO server's own Python (3.11, NumPy 2.4.6). SciPy 1.17.1 is added to it as a pinned add-on, following [../README.md](../README.md). No separate environment. |
| Input | Image IDs, or exactly one dataset ID. An optional wildcard filters by full image name. |
| Channels | Up to 6. One radius field per channel, in stored channel order. Every channel needs a value; 0 means copy unchanged. |
| Batch | Same channel count and same ordered (trimmed) channel names across all images. X/Y/Z/T may vary. |
| Algorithm | The pinned port, unchanged. Presmooth on, automatic shrink, dark background, corrected negatives clipped to 0. |
| Pixel types | uint8, uint16, float32. Each output keeps its source's type. |
| Destination | Exactly one: an existing dataset ID, or a new dataset name. |
| Naming | Fixed: `{basename}_bgs-{r1}-…-{rC}.tif` |
| Safety | A dry run (the default) and a full metadata check before any write. Source images are never modified. |
| Failures | A failed image does not stop the batch. Its partial output is deleted, and the message lists the failure. |
| Provenance | A description and a key-value annotation on every output. |

## 2. Processing

- Every Z, C and T plane is processed independently as a 2D raw plane read with `getPlane(z, c, t)`.
- Radii are XY pixels. They must be finite and ≥ 0, and fractional values are allowed. The canonical value (§5) drives both processing and naming.
- **Radius 0:** an exact copy, with no smoothing, clipping or type conversion.
- **Positive radius:**
  1. `rolling_ball_background(plane, r, presmooth=True, light_background=False)`.
  2. `subtract_background_image` subtracts the result from the raw plane and clips negatives to 0.
  3. uint8/uint16: clip to the type range, `floor(x + 0.5)`, then cast once.
  4. float32: require finite values within the float32 range, then cast once.
- Float planes containing NaN or Inf are rejected, including in copied channels.
- Not supported: 3D or cross-time processing, light-background mode, shrink override, tiling of large planes.

## 3. Dialog

| Parameter | Type / default | Meaning |
|---|---|---|
| `Data_Type` | Image \| Dataset, default Image | What `IDs` refers to |
| `IDs` | List of Long, required | Image IDs, or exactly one dataset ID |
| `Name_Filter` | String, optional | Wildcard (`*`, `?`) on the full name; blank includes all |
| `Channel_1_Radius` … `Channel_6_Radius` | Double ≥ 0, optional, **no default** | Radius per channel; 0 keeps the channel unchanged |
| `Output_Dataset_ID` | Long, optional | Existing destination dataset |
| `New_Dataset_Name` | String, optional | Create a destination dataset with this name |
| `Dry_Run` | Bool, default True | Check and list the plan without writing |

A blank radius means "not set" and is never treated as 0. The script description and each radius field's help both state that 0 keeps the channel unchanged.

## 4. Checks before any write

The run fails with all errors listed, and nothing written, if any of these is true:
- Dataset input does not have exactly one ID.
- An ID is not found or not accessible.
- No image matches.
- A pixel type is unsupported.
- An image has more than 6 channels.
- An image's channel names or order differ from those of the lowest-ID image.
- An image needs a pixel pyramid, which rules out whole-plane reads.
- A channel has no radius, or a radius is given for a channel the images lack.
- An output name is invalid or longer than 255 characters.
- Not exactly one destination is given.
- The existing destination is missing, or the user cannot link to it.

## 5. Output

**Name.**
1. Take the full stored image name.
2. Remove a final `.ome.tiff`, `.ome.tif`, `.tiff` or `.tif`, ignoring case and trying the longest first. For other names, remove the last dot-suffix unless the dot is the first or last character.
3. Append `_bgs-` and the radii in channel order, joined by `-`, then `.tif`.

Radii are written in their shortest decimal form: `50.0 → 50`, `12.50 → 12.5`, `1e2 → 100`. Names are never truncated or de-duplicated. Image IDs distinguish repeated runs. `.tif` is a label only; no TIFF file is created.

**Image.** For each source image, the script:
1. calls `copyAndResizeImage` on the source, which copies dimensions, pixel type, physical sizes and channel metadata, but not pixels;
2. sets the name and description;
3. uploads planes with `RawPixelsStore.setPlane(z, c, t)`;
4. sets each channel's min/max;
5. adds the key-value annotation (namespace `org.jeffleelab.omero.bgsub`), which records the source ID and name, radius per channel, settings, algorithm commit and SHA-256, script version and UTC time;
6. **last**, links the image to the destination dataset.

If any step fails, the script deletes that new image and its annotation. Not copied: per-plane timing and positions, ROIs, tags, attachments, rendering settings.

**Destination.** A new dataset is created only after all checks pass and only in a real run, never in a dry run. It is not linked to a project.

**Result.** The script returns:
- `Message`: counts, the channel mapping, and failures (first 50 lines);
- `Dataset`;
- `Image`, when exactly one image was produced.

## 6. Verification

- **Local:** `test_local.py` covers:
  - the pinned block, byte for byte;
  - naming;
  - copying, rounding and NaN rejection;
  - agreement with the pinned module;
  - the dialog declaration;
  - preflight and dry-run messages, using fake OMERO objects.
- **Live:** the checklist in [README.md](README.md#live-check) covers the SciPy environment, a dry run, exact copying when every radius is 0, agreement of positive-radius pixels with a local recalculation, metadata and links, and a run as a non-admin user.

## 7. Deliberately left out (v0.1 features)

- a separate worker environment and subprocess protocol;
- the external adapter package and operator config;
- the durable journal, resume and admission lock;
- lifecycle states;
- resource limits;
- CSV/JSON report attachments;
- full read-back hashing and strict metadata re-verification;
- per-plane metadata copy;
- Name/Index channel matching;
- the project link.

Add one back only when a concrete need appears.
