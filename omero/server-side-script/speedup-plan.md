# Speed-up plan: pixel reading in the server-side scripts

Status: Steps 1, 2, 4 and 5 **implemented** on 2026-11-03 (Z_Projection 0.3.0, Rolling_Ball_Background 0.5.0); local tests pass. Step 3 is **not done**: the server's `omero.pixeldata.max_plane_*` values are unknown, so the fallback applies and `needs_pyramid()` is unchanged. The live check in Section 3 is still to do. Written 2026-11-03 after profiling the scripts and the server.

Scope: `z-project/Z_Projection.py` and `subtract-background/Rolling_Ball_Background.py`. Both read pixels the same way, so both get the same fix. Output pixels, names, types, metadata and dialog parameters must stay exactly the same. This is a performance change only.

## 1. Background: why the scripts are slow

### 1.1 Server facts (checked over SSH, read-only)

- Host `OmeroDavisVM.mvls.gla.ac.uk`: a Hyper-V VM with 8 vCPUs (Xeon Silver 4310, 2.1 GHz) and 62 GB RAM. CPU was mostly idle and I/O wait was 0% when checked.
- OMERO.server 5.6.18 runs in Docker (`glasgow-it-omero-compose`), together with OMERO.web and PostgreSQL.
- The OMERO data directory is on NFS: `omero-davis.mvls.gla.ac.uk:/tank/new-storage`.
  - Metadata operations take about 1 ms each, reads about 3.4 ms and writes 14–23 ms.
  - Every pixel read goes from OMERO.server to Bio-Formats to NFS.
- The repository is mostly TIFF (about 6,800 files) and Olympus VSI/ETS (about 870 files). VSI is one of the slowest formats for Bio-Formats to open.

Hardware is not the bottleneck. The bottleneck is how pixels are requested.

### 1.2 Problem A: each plane reopens the original file

Both scripts read pixels through `image.getPrimaryPixels().getPlane(z, c, t)`:

- `Z_Projection.py:272`, used in `output_planes()` at line 134
- `Rolling_Ball_Background.py:434`, used in `output_planes()` at line 288

In omero-py (`omero/gateway/__init__.py`, `PixelsWrapper.getPlane` at about line 7539, then `getPlanes`, then `getTiles` at about line 7559), **every** `getPlane()` call does this:

1. `_prepareRawPixelsStore()` creates a new `RawPixelsStore` and calls `setPixelsId(id, True)`. The server then builds a pixel buffer, which for imported images means opening the original file with Bio-Formats and reading its memo file from `BioFormatsCache` on NFS.
2. It reads one plane.
3. It closes the store, which discards the open reader.

So an image with Z × C × T planes is opened Z × C × T times. For example, 40 Z × 3 C × 1 T means 120 opens of the same file. Each open can take a fraction of a second for a TIFF, and several seconds for a VSI.

### 1.3 Problem B: pure-Python byte decoding

`getTiles()` decodes each plane with `struct.unpack('>%d%s' % (n, char), raw)`. That builds a Python tuple with one entry per pixel, which is then copied into a numpy array. Measured on the Mac (Apple silicon) in the `omero` conda env:

| Plane | `getPlane`-style decode | `np.frombuffer` | Ratio |
|---|---|---|---|
| 1024×1024 uint16 | 30 ms | 0.25 ms | ~120× |
| 2048×2048 uint16 | 134 ms | 1 ms | ~130× |

Expect roughly 2–3 times longer on the server's 2.1 GHz Xeon.

### 1.4 Problem C: `needs_pyramid()` opens every image one more time

`needs_pyramid()` is at `Z_Projection.py:141` and `Rolling_Ball_Background.py:307`. It is called from `plan_batch()` for every image in the batch, and it does this:

```python
store = image.getPrimaryPixels()._prepareRawPixelsStore()   # createRawPixelsStore + setPixelsId(id, True)
return store.requiresPixelsPyramid()
```

`setPixelsId(..., True)` is the same expensive call as in Problem A. It opens the original file with Bio-Formats, only to answer a yes/no question about the image's size. The store is then closed, and `process_image()` opens the file again.

Why the check exists: OMERO refuses `getPlane()` on images bigger than its maximum plane size, so those images can't be processed as whole planes. Catching them in `plan_batch()` lets the whole batch be rejected before anything is written, which these scripts are designed to do.

What it costs:
- Today it is one open out of Z × C × T + 1 per image, so small next to Problem A.
- After Problem A is fixed, it becomes **half** of all file opens (2 per image instead of 1).
- It is the **whole** cost of a `Dry_Run`. A dry run over a dataset of 100 VSI images opens 100 files only to check their sizes.

## 2. Changes

Do the steps in order and run the local tests after each one. Steps 1 and 2 are the main win. Step 3 is a smaller, separate improvement. Step 4 is instrumentation.

### Step 1: one reader per image, decoded with `np.frombuffer` (both scripts)

Add a context manager to each script. Each script must stay a single self-contained file, because OMERO runs scripts as lone files, so copy the helper into both. Use `contextlib`, which is in the standard library, so no new dependency is needed.

```python
import contextlib

# Z_Projection.py already has NUMPY_TYPES. Rolling_Ball_Background.py needs its own:
READ_TYPES = {"uint8": np.uint8, "uint16": np.uint16, "float": np.float32}


@contextlib.contextmanager
def plane_reader(conn, image):
    """Yield get_plane(z, c, t) for image, reading every plane through one open RawPixelsStore.

    omero-py's PixelsWrapper.getPlane() opens a new store (and so the original file, through
    Bio-Formats) for every plane, and decodes with struct.unpack. This opens the file once per
    image and decodes with np.frombuffer. Returns native-byte-order, writable 2D arrays (y, x).
    """
    size_x, size_y = image.getSizeX(), image.getSizeY()
    wire = np.dtype(READ_TYPES[pixel_type(image)]).newbyteorder(">")   # OMERO sends big-endian
    expected = size_x * size_y * wire.itemsize
    store = conn.c.sf.createRawPixelsStore()
    try:
        store.setPixelsId(image.getPixelsId(), True, conn.SERVICE_OPTS)

        def get_plane(z, c, t):
            raw = store.getPlane(z, c, t, conn.SERVICE_OPTS)
            if len(raw) != expected:
                raise ValueError(f"Plane z={z} c={c} t={t}: got {len(raw)} bytes, expected {expected}")
            return np.frombuffer(raw, dtype=wire).reshape(size_y, size_x).astype(wire.newbyteorder("="))

        yield get_plane
    finally:
        store.close(conn.SERVICE_OPTS)
```

Notes for the implementer:

- **Shape is (size_y, size_x)**: rows, then columns. The tests must use a non-square image so that a swap is caught.
- **`.astype(native)` copies the data.** This is intended. `np.frombuffer` returns a read-only array in the wire byte order, and the code downstream needs writable arrays in native order. `project()` writes into `result` with `out=`, the writes call `result.dtype.newbyteorder(">")`, and `scipy.ndimage` runs on the planes.
- **`"float"` in OMERO is 32-bit** (`>f4`). These scripts reject `double`, `int*` and `uint32` sources in `plan_batch`, so the mapping only needs uint8, uint16 and float.
- In Rolling_Ball_Background, the pixel type comes from `image.getPrimaryPixels().getPixelsType().getValue()`, which `plan_batch` already uses. Add a small `pixel_type(image)` helper like the one in Z_Projection, or inline that call.
- `ImageWrapper.getPixelsId()` exists. The scripts already call it on `new_image`.

Wiring, in `process_image()` of each script: replace the `image.getPrimaryPixels().getPlane` / `source_pixels.getPlane` argument with the reader. Keep the reader open around the existing write-store block:

```python
with plane_reader(conn, image) as get_plane:
    store = conn.c.sf.createRawPixelsStore()
    try:
        store.setPixelsId(new_image.getPixelsId(), True, conn.SERVICE_OPTS)
        for z, c, t, result in output_planes(get_plane, ...):   # unchanged otherwise
            ...
    finally:
        store.close(conn.SERVICE_OPTS)
```

The reader must stay inside the existing `try/except` that deletes the partial output image on failure, so that a read error still cleans up. In Rolling_Ball_Background, remove the `source_pixels = image.getPrimaryPixels()` line if nothing else uses it.

Leave `output_planes()`, `project()`, `correct_plane()`, `z_max()` and the copied rolling-ball block unchanged. They already take a `get_plane` callable, so the existing tests keep passing.

### Step 2: tests for the reader (both `test_local.py` files)

Add a `PlaneReader` test class to each `test_local.py`, with fakes for the store and the connection. Don't touch the existing fakes beyond adding methods they now need.

```python
class FakeStore:
    def __init__(self, stack, wire):            # stack: (z, c, t, y, x) native array
        self.stack, self.wire = stack, wire
        self.pixels_ids, self.reads, self.closed = [], [], False

    def setPixelsId(self, pixels_id, bypass, ctx=None):
        self.pixels_ids.append(pixels_id)

    def getPlane(self, z, c, t, ctx=None):
        self.reads.append((z, c, t))
        return self.stack[z, c, t].astype(self.wire).tobytes()

    def close(self, ctx=None):
        self.closed = True
```

Then give a fake connection `c=SimpleNamespace(sf=SimpleNamespace(createRawPixelsStore=lambda: store))` and `SERVICE_OPTS=None`. Give a fake image `getPixelsId`, `getSizeX`, `getSizeY` and the pixel type. To build the wire bytes, use `arr.astype(">u2")` and similar, not `np.random.randint(..., dtype=">u2")`, which is deprecated in NumPy 2.

Cases to cover:

1. **Round trip for uint8, uint16 and float**, on a **non-square** plane (e.g. 5 rows × 7 columns). The result equals the source, its dtype is native (`result.dtype.byteorder in "=|"`), and it is writable (`result.flags.writeable`).
2. **One store per image:** reading all Z × C × T planes calls `createRawPixelsStore` and `setPixelsId` exactly once.
3. **The store is closed** after normal exit, and also when the body raises an exception.
4. **Wrong byte count** (the fake returns a truncated buffer): raises `ValueError` with the plane index in the message.
5. **End to end with the real `output_planes()`:** feed `get_plane` from the reader into the script's `output_planes()` and check that the output matches the existing direct-array tests. For Z_Projection, use SUM and a Z range. For bgsub, use MIP_FIRST with a nonzero radius.

Run from each script folder in the `omero` conda env (`conda activate omero`):

```sh
python -m unittest test_local -v
```

All existing tests must still pass, including `AlgorithmIsCopied` and `ImageJParity` in subtract-background.

### Step 3: stop `needs_pyramid()` from opening files (recommended)

Goal: keep the "reject too-large images before writing anything" behaviour, without opening every file during planning.

**Recommended approach: check dimensions in `plan_batch`, then confirm on the store that is open anyway.**

1. **In `plan_batch()`**, decide from the image size, which comes from the database and needs no file open:

   ```python
   # OMERO.server needs a pyramid (and refuses whole-plane reads) when sizeX * sizeY exceeds
   # omero.pixeldata.max_plane_width * omero.pixeldata.max_plane_height (default 3192 x 3192).
   MAX_PLANE_PIXELS = 3192 * 3192

   def too_large(image):
       return image.getSizeX() * image.getSizeY() > MAX_PLANE_PIXELS
   ```

2. **In `process_image()`**, after `setPixelsId` inside `plane_reader`, call `store.requiresPixelsPyramid()` on the **already open** store. This costs no extra file open. If it returns True, raise a `ValueError` with the same "too large for whole-plane processing" wording, so the existing per-image failure path deletes the partial output. This is a safety net in case the size rule in point 1 doesn't match the server exactly. Put the check before any `setPlane` write.

The implementer must verify before relying on point 1:

- **Read the OMERO 5.6 source.** `ome.io.nio.PixelsService.requiresPixelsPyramid(Pixels)` in `ome/openmicroscopy` (`components/romio`) should confirm that the rule is `sizeX * sizeY > maxPlaneWidth * maxPlaneHeight`. It may or may not also consider native Bio-Formats resolution levels, which matters for VSI. If the rule is different, mirror it or keep the old check.
- **Get the server's actual values** of `omero.pixeldata.max_plane_width` and `omero.pixeldata.max_plane_height`. Running `omero config get` inside the server container needs sudo, so ask the user or IT. If they're not set, the defaults apply (3192).
- **Update the fake images** in both `test_local.py` files. Preflight tests currently use `pyramid=True` through `_prepareRawPixelsStore().requiresPixelsPyramid()`. Change them to set sizes (`getSizeX`/`getSizeY`) above and below the threshold. Keep a test that `"too large"` appears in the dry-run message. Add a test that the safety net in `process_image` raises when the open store reports a pyramid. That needs a small fake around `process_image`, or else test the reader's check on its own.

**Fallback, if the rule can't be confirmed:** leave `needs_pyramid()` as it is. It is correct, just slower: one extra file open per image. Don't ship a dimension rule that hasn't been checked against the server.

### Step 4: per-image timing in the job output

Make the existing per-image `print()` lines (`Image {index}/{len(plan)}: ...`) report time spent, so that the gain can be measured on the server and future slowness can be diagnosed. Prints go to the job's stdout file, which OMERO.web can show. They don't go into the `Message` output, so that stays unchanged.

- Time the reading inside `get_plane`, for example by having `plane_reader` keep a running total in a small mutable holder.
- Time `store.setPlane` (writing).
- Report total time per image. Compute time is total minus read minus write.

For example: `Image 3/12: 1234 cells.tif done in 14.2 s (read 1.1 s, write 0.6 s)`. Keep it one line per image, using `time.perf_counter()`.

### Step 5: version bumps and documentation

- Bump `VERSION`: Z_Projection from `0.2.0` to `0.3.0`, and Rolling_Ball_Background from `0.4.2` to `0.5.0`. The version is written into each output's provenance annotation, so outputs from the faster version can be told apart.
- In each script's `README.md`, add a short "Performance" note: one store per image, frombuffer decoding, and the timing line in stdout. If Step 3 was done, also note that dry runs no longer open the image files.
- Follow `../AGENTS.md`: don't describe anything as verified on the server until the live check in Section 3 has been done. The z-project README currently says the script "has not yet been run on the OMERO server". Update that only from evidence the user provides.

## 3. Live verification on the server (needs the user)

The agent can't deploy. Installing a script needs OMERO admin rights; see `README.md`, "Installing a script". Ask the user to do this:

1. **Baseline:** pick a fixed test set: one small multichannel uint16 TIFF z-stack and one image imported from VSI, each with at least 20 Z-planes. Run the **current** installed versions on it and note the run times from the OMERO.web activities panel.
2. Install the new versions (**Replace script**, which keeps the same IDs).
3. Run the new versions on the same images with the same parameters into a separate dataset, and compare the times. The new stdout timing lines show where time is spent.
4. **Check the pixels are identical.** From the Mac, in the `omero` env after `omero login`, compare each old output with its new output plane by plane:

   ```python
   from omero.gateway import BlitzGateway
   import numpy as np
   conn = BlitzGateway(host=HOST, port=4064, username=USER, passwd=PASS); conn.connect()   # from the user
   old, new = conn.getObject("Image", OLD_ID), conn.getObject("Image", NEW_ID)
   zct = [(z, c, t) for z in range(old.getSizeZ()) for c in range(old.getSizeC()) for t in range(old.getSizeT())]
   same = all(np.array_equal(a, b) for a, b in zip(old.getPrimaryPixels().getPlanes(zct),
                                                   new.getPrimaryPixels().getPlanes(zct)))
   print("identical" if same else "DIFFERENT")
   ```

   Also confirm that the pixel type, sizes and channel names match. The provenance annotation differs only in `script_version` and `created_utc`.
5. If Step 3 was done, also: give the dry run an image bigger than 3192×3192, if one exists, and confirm it is rejected as "too large".

## 4. Constraints: do not

- Edit anything between the `--- BEGIN copied from` and `--- END copied from` markers in `Rolling_Ball_Background.py`. `AlgorithmIsCopied` checks it against `misc/python-rolling-ball/`.
- Change output names, pixel types, rounding or clipping, dialog parameter names, defaults, or the "validate whole batch before writing" behaviour.
- Add dependencies, split a script into several files, or import shared helpers from a separate module. Each script must run as a lone file with the server's Python: 3.11, NumPy 2.4.6 and SciPy 1.17.1, which match the local `omero` env.
- Add multiprocessing or threads. Scripts run inside OMERO's Processor service on a shared server. Parallelism is out of scope for this change.
- Create a new conda environment. Use `conda activate omero`.
- Commit or push unless the user asks.

## 5. Out of scope, for the record

- **Rolling-ball compute time.** For radius ≤ 10, ImageJ's rule uses no shrinking, so each plane gets a full-resolution 17×17 non-flat erosion plus dilation: several seconds per 2048² plane. That is genuine work that matches ImageJ. The existing "Max projection, then subtract" option is the way to reduce it.
- **Overall OMERO.web and OMERO.figure slowness.** This is server configuration, a change for IT, not for these scripts:
  - gunicorn runs only 5 sync workers with 1 thread each, so suggest `omero.web.wsgi_worker_class=gthread` with more threads and workers;
  - the Java heaps are at their defaults: Blitz and PixelData about 4.6 GB each and Indexer 3 GB, out of 62 GB RAM;
  - every rendered plane is read from VSI or TIFF over NFS.
- **Converting slow formats** (VSI) to OME-Zarr or tiled OME-TIFF before import.
- **The VM's root disk is 90% full** (122 GB), probably `/var/lib/docker`. This was reported to the user separately.
