#!/usr/bin/env python
"""Rolling-ball background subtraction for OMERO images.

Reads raw planes, applies the lab's pinned ImageJ rolling-ball port with one
radius per channel, and writes new images into one explicitly chosen dataset.
Source images are never modified. Needs SciPy in the OMERO server's Python
environment (see ../README.md).
"""
from __future__ import annotations

import datetime
import fnmatch
import functools
import math
from decimal import Decimal

import numpy as np
import omero
import omero.scripts as scripts
from omero.gateway import BlitzGateway, DatasetWrapper, MapAnnotationWrapper
from omero.rtypes import rint, rlong, robject, rstring

try:
    from scipy import ndimage as ndi
except ImportError:  # reported when the script runs, so it still lists in OMERO.web
    ndi = None

VERSION = "0.4.1"
MAX_CHANNELS = 8
MAX_NAME_LENGTH = 255
# Only these endings are removed to get the basename; anything else after a dot stays in the name.
# Compound extensions come first, so .ome.tif is removed whole.
IMAGE_EXTENSIONS = (".ome.tiff", ".ome.tif", ".ome.btf", ".ome.zarr", ".tiff", ".tif", ".btf",
                    ".lif", ".czi", ".nd2", ".vsi", ".lsm", ".oib", ".oif", ".oir", ".ims", ".zvi",
                    ".dv", ".r3d", ".ics", ".ids", ".lei", ".stk", ".nd", ".svs", ".ndpi", ".scn",
                    ".mrxs", ".dcm", ".mrc", ".zarr", ".h5", ".png", ".jpg", ".jpeg", ".bmp", ".gif")
MAX_MESSAGE_LINES = 50
NAMESPACE = "org.jeffleelab.omero.bgsub"
ALGORITHM_COMMIT = "e6360e0a7e04580ac30e143281edae66660f9ec6"
ALGORITHM_SHA256 = "0713d5360395a9cf8748829b2f782d316fa3be57df1bc0827adff8a90af55c9c"
SETTINGS = ("presmooth on; automatic shrink; dark background; "
            "corrected negatives clipped to 0; source pixel type kept")
SUPPORTED_TYPES = ("uint8", "uint16", "float")
RADIUS_HELP = "Radius in XY pixels. 0 = skip subtraction and keep this channel unchanged."
PROJECTIONS = ("Off", "Max projection, then subtract", "Subtract, then max projection")
PROJECTION_OFF, MIP_FIRST, MIP_LAST = PROJECTIONS


# Pinned rolling-ball algorithm, copied verbatim (lines 21-206) from
# https://github.com/JeffLeeLab/Image-analysis/blob/e6360e0a7e04580ac30e143281edae66660f9ec6/people/AlfredoCastello/Zhiling-Marko-HIV/src/imagej_rolling_ball.py
# (file SHA-256 ALGORITHM_SHA256). It ports ImageJ's BackgroundSubtracter.java and
# comes from the owner's RDI-calculator project (rdi/background.py, commit d30fae7),
# where it was validated. Do not edit: test_local.py checks it against the pinned file.
# --- BEGIN pinned imagej_rolling_ball.py ---
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


# --- END pinned imagej_rolling_ball.py ---


def radius_token(value):
    """Canonical radius text: 50.0 -> "50", 12.50 -> "12.5", 1e2 -> "100"."""
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("Radius must be finite and nonnegative")
    if number == 0:
        return "0"
    token = format(Decimal(repr(number)), "f")
    return token.rstrip("0").rstrip(".") if "." in token else token


def output_name(name, radii, projection=PROJECTION_OFF):
    """Fixed output name: {basename}_bgs-{r0}-{r1}-....tif, with _mip placed in step order.

    The basename drops only a known image extension, so 'cells_0.5uM' and OMERO series names such as
    'Experiment.lif [Position 10]' are kept whole.
    """
    base = name
    for extension in IMAGE_EXTENSIONS:
        if name.lower().endswith(extension):
            base = name[:-len(extension)]
            break
    if not base:
        raise ValueError(f"Image name '{name}' has an empty basename")
    bgs = "_bgs-" + "-".join(radius_token(r) for r in radii)
    steps = {MIP_FIRST: "_mip" + bgs, MIP_LAST: bgs + "_mip"}.get(projection, bgs)
    result = base + steps + ".tif"
    if len(result) > MAX_NAME_LENGTH:
        raise ValueError(f"Output name for '{name}' exceeds {MAX_NAME_LENGTH} characters")
    return result


def correct_plane(plane, radius):
    """Background-subtract one 2D plane; radius 0 returns an exact copy."""
    if plane.dtype.kind == "f" and not np.isfinite(plane).all():
        raise ValueError("Plane contains NaN or infinite values")
    if radius == 0:
        return plane.copy()
    background = rolling_ball_background(plane, radius, presmooth=True, light_background=False)
    corrected, _ = subtract_background_image(plane, background)
    if plane.dtype.kind == "u":
        maximum = np.iinfo(plane.dtype).max
        # Clip to the type range, round half up, then cast once.
        return np.floor(np.clip(corrected, 0, maximum) + 0.5).astype(plane.dtype)
    if not np.isfinite(corrected).all() or corrected.max() > np.finfo(np.float32).max:
        raise ValueError("Corrected values are not representable as float32")
    return corrected.astype(plane.dtype)


def z_max(planes):
    """Pixel-wise maximum over z. NaN carries through, so correct_plane still rejects it."""
    return functools.reduce(np.maximum, planes)


def output_planes(get_plane, size_z, size_c, size_t, radii, projection):
    """Yield (z, c, t, plane) for the output; a projected output has one z-plane."""
    for c in range(size_c):
        for t in range(size_t):
            stack = (get_plane(z, c, t) for z in range(size_z))
            if projection == MIP_FIRST:
                yield 0, c, t, correct_plane(z_max(stack), radii[c])
            elif projection == MIP_LAST:
                yield 0, c, t, z_max(correct_plane(plane, radii[c]) for plane in stack)
            else:
                for z, plane in enumerate(stack):
                    yield z, c, t, correct_plane(plane, radii[c])


def channel_mapping(names, radii):
    return ", ".join(f"{i + 1} {name or '(unnamed)'}={radius_token(r)}" + (" (copied)" if r == 0 else "")
                     for i, (name, r) in enumerate(zip(names, radii)))


def channel_names(image):
    return [(ch.getLogicalChannel().name or "").strip() for ch in image.getChannels(noRE=True)]


def needs_pyramid(image):
    store = image.getPrimaryPixels()._prepareRawPixelsStore()
    try:
        return store.requiresPixelsPyramid()
    finally:
        store.close()


def collect_images(conn, params):
    """Selected images (or one dataset's images), name-filtered, sorted by ID."""
    ids = params["IDs"]
    if params["Data_Type"] == "Dataset":
        if len(ids) != 1:
            raise ValueError("Dataset input needs exactly one dataset ID")
        dataset = conn.getObject("Dataset", ids[0])
        if dataset is None:
            raise ValueError(f"Dataset {ids[0]} not found or not accessible")
        images = list(dataset.listChildren())
    else:
        images = []
        for image_id in sorted(set(ids)):
            image = conn.getObject("Image", image_id)
            if image is None:
                raise ValueError(f"Image {image_id} not found or not accessible")
            images.append(image)
    pattern = params.get("Name_Filter") or ""
    if pattern:
        images = [image for image in images if fnmatch.fnmatchcase(image.getName(), pattern)]
    return sorted({image.getId(): image for image in images}.values(), key=lambda image: image.getId())


def plan_batch(images, params):
    """Check the whole batch before any write. Returns (names, radii, [(image, output name)])."""
    errors = []
    reference = channel_names(images[0])
    for image in images:
        label = f"Image {image.getId()} '{image.getName()}'"
        pixel_type = image.getPrimaryPixels().getPixelsType().getValue()
        if pixel_type not in SUPPORTED_TYPES:
            errors.append(f"{label}: pixel type {pixel_type} is not supported (uint8, uint16, float)")
        names = channel_names(image)
        if len(names) > MAX_CHANNELS:
            errors.append(f"{label}: {len(names)} channels, but the form has {MAX_CHANNELS} radius fields")
        elif names != reference:
            errors.append(f"{label}: channels {names} differ from {reference} in image {images[0].getId()}")
        if needs_pyramid(image):
            errors.append(f"{label}: too large for whole-plane processing")
    size_c = len(reference)
    given = [params.get(f"Channel_{i}_Radius") for i in range(1, MAX_CHANNELS + 1)]
    missing = [str(i + 1) for i in range(min(size_c, MAX_CHANNELS)) if given[i] is None]
    extra = [str(i + 1) for i in range(size_c, MAX_CHANNELS) if given[i] is not None]
    if missing:
        errors.append(f"Enter a radius for channel(s) {', '.join(missing)}; use 0 to keep a channel unchanged")
    if extra:
        errors.append(f"Images have {size_c} channel(s); clear the radius for channel(s) {', '.join(extra)}")
    plan = []
    if not errors:
        # Canonical values drive both processing and names.
        radii = [float(radius_token(r)) for r in given[:size_c]]
        projection = params.get("Z_Projection", PROJECTION_OFF)
        for image in images:
            try:
                plan.append((image, output_name(image.getName(), radii, projection)))
            except ValueError as exc:
                errors.append(str(exc))
    if errors:
        raise ValueError("\n".join(errors))
    return reference, radii, plan


def resolve_destination(conn, params):
    """Existing dataset wrapper, or None plus the name of a dataset to create."""
    dataset_id = params.get("Output_Dataset_ID")
    new_name = (params.get("New_Dataset_Name") or "").strip()
    if (dataset_id is None) == (not new_name):
        raise ValueError("Give exactly one of Output_Dataset_ID or New_Dataset_Name")
    if new_name:
        if len(new_name) > MAX_NAME_LENGTH:
            raise ValueError(f"New dataset name exceeds {MAX_NAME_LENGTH} characters")
        return None, new_name
    dataset = conn.getObject("Dataset", dataset_id)
    if dataset is None:
        raise ValueError(f"Dataset {dataset_id} not found or not accessible")
    if not dataset.canLink():
        raise ValueError(f"You cannot add images to dataset {dataset_id}")
    return dataset, None


def add_provenance(conn, new_image, source, mapping, projection):
    annotation = MapAnnotationWrapper(conn)
    annotation.setNs(NAMESPACE)
    annotation.setValue([
        ["source_image_id", str(source.getId())],
        ["source_image_name", source.getName()],
        ["radii_by_channel", mapping],
        ["z_projection", projection],
        ["settings", SETTINGS],
        ["algorithm_commit", ALGORITHM_COMMIT],
        ["algorithm_sha256", ALGORITHM_SHA256],
        ["script_version", VERSION],
        ["created_utc", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")],
    ])
    annotation.save()
    new_image.linkAnnotation(annotation)


def process_image(conn, image, radii, name, dataset, mapping, projection):
    """Write one background-subtracted copy of image into dataset."""
    pixels_service = conn.getPixelsService()
    update = conn.getUpdateService()
    size_z, size_c, size_t = image.getSizeZ(), image.getSizeC(), image.getSizeT()
    out_z = size_z if projection == PROJECTION_OFF else 1
    # Copies dimensions, pixel type, physical sizes and channel metadata, not pixels.
    new_id = pixels_service.copyAndResizeImage(
        image.getId(), rint(image.getSizeX()), rint(image.getSizeY()), rint(out_z), rint(size_t),
        list(range(size_c)), None, False, conn.SERVICE_OPTS).getValue()
    try:
        new_image = conn.getObject("Image", new_id)
        new_image._obj.setName(rstring(name))
        new_image._obj.setDescription(rstring(
            f"Rolling-ball background subtraction of image {image.getId()} ({image.getName()}). "
            f"Radii by channel: {mapping}. Z projection: {projection}. {SETTINGS}."))
        update.saveObject(new_image._obj, conn.SERVICE_OPTS)
        source_pixels = image.getPrimaryPixels()
        ranges = [[math.inf, -math.inf] for _ in range(size_c)]
        store = conn.c.sf.createRawPixelsStore()
        try:
            store.setPixelsId(new_image.getPixelsId(), True, conn.SERVICE_OPTS)
            for z, c, t, result in output_planes(source_pixels.getPlane, size_z, size_c, size_t,
                                                 radii, projection):
                wire = np.ascontiguousarray(result, dtype=result.dtype.newbyteorder(">"))
                store.setPlane(wire.tobytes(), z, c, t, conn.SERVICE_OPTS)
                ranges[c] = [min(ranges[c][0], float(result.min())),
                             max(ranges[c][1], float(result.max()))]
        finally:
            store.close(conn.SERVICE_OPTS)
        for c, (low, high) in enumerate(ranges):
            pixels_service.setChannelGlobalMinMax(new_image.getPixelsId(), c, low, high, conn.SERVICE_OPTS)
        add_provenance(conn, new_image, image, mapping, projection)
        # Link last, so only finished images appear in the dataset.
        link = omero.model.DatasetImageLinkI()
        link.setParent(omero.model.DatasetI(dataset.getId(), False))
        link.setChild(omero.model.ImageI(new_id, False))
        update.saveObject(link, conn.SERVICE_OPTS)
    except Exception as exc:
        try:
            conn.deleteObjects("Image", [new_id], deleteAnns=True, wait=True)
        except Exception:
            raise RuntimeError(f"{exc} (partial output image {new_id} could not be deleted)") from exc
        raise
    return new_image


def limited(lines):
    if len(lines) <= MAX_MESSAGE_LINES:
        return lines
    return lines[:MAX_MESSAGE_LINES] + [f"... and {len(lines) - MAX_MESSAGE_LINES} more"]


def run(conn, params):
    """Returns (message, {output name: OMERO model object})."""
    if ndi is None:
        return "SciPy is not installed in the OMERO server's Python environment; ask the OMERO administrator.", {}
    try:
        images = collect_images(conn, params)
        if not images:
            return "No images matched the selection and name filter. Nothing was written.", {}
        names, radii, plan = plan_batch(images, params)
        dataset, new_name = resolve_destination(conn, params)
    except ValueError as exc:
        return "Nothing was written.\n" + str(exc), {}
    mapping = channel_mapping(names, radii)
    projection = params.get("Z_Projection", PROJECTION_OFF)
    if params.get("Dry_Run", False):
        target = f"dataset {dataset.getId()} '{dataset.getName()}'" if dataset else f"new dataset '{new_name}'"
        lines = [f"Dry run: {len(plan)} image(s) -> {target}.", f"Channels: {mapping}.",
                 f"Z projection: {projection}."]
        lines += limited([f"{image.getId()} {image.getName()} -> {name}" for image, name in plan])
        return "\n".join(lines + ["Untick Dry_Run to process."]), {}
    if dataset is None:
        dataset = DatasetWrapper(conn, obj=omero.model.DatasetI())
        dataset.setName(rstring(new_name))
        dataset.save()
    done, failed = [], []
    for index, (image, name) in enumerate(plan, start=1):
        print(f"Image {index}/{len(plan)}: {image.getId()} {image.getName()}", flush=True)
        try:
            done.append(process_image(conn, image, radii, name, dataset, mapping, projection))
        except Exception as exc:
            failed.append(f"{image.getId()} {image.getName()}: {type(exc).__name__}: {exc}")
            print(failed[-1], flush=True)
    lines = [f"{len(done)} of {len(plan)} image(s) written to dataset {dataset.getId()} '{dataset.getName()}'.",
             f"Channels: {mapping}.", f"Z projection: {projection}."]
    if failed:
        lines += ["Failed (no output kept):"] + limited(failed)
    outputs = {"Dataset": dataset._obj}
    if len(done) == 1:
        outputs["Image"] = done[0]._obj
    return "\n".join(lines), outputs


def parameters():
    params = [
        scripts.String("Data_Type", optional=False, grouping="01", default="Image",
                       values=[rstring("Image"), rstring("Dataset")],
                       description="Process the selected images, or every image in one dataset."),
        scripts.List("IDs", optional=False, grouping="02",
                     description="Image IDs, or exactly one dataset ID.").ofType(rlong(0)),
        scripts.String("Name_Filter", grouping="03",
                       description="Optional wildcard on the full image name (* and ?), e.g. *.tif. "
                                   "Blank processes all, including earlier _bgs- results."),
    ]
    for i in range(1, MAX_CHANNELS + 1):
        params.append(scripts.Double(f"Channel_{i}_Radius", grouping=f"{3 + i:02d}", min=0,
                                     description=f"Channel {i}. {RADIUS_HELP}"))
    params += [
        scripts.String("Z_Projection", grouping=f"{4 + MAX_CHANNELS:02d}", default=PROJECTION_OFF,
                       values=[rstring(p) for p in PROJECTIONS],
                       description="Optional max-intensity projection along Z; the output has one Z-plane. "
                                   "Projecting first runs the rolling ball once per channel and timepoint "
                                   "instead of once per slice."),
        scripts.Long("Output_Dataset_ID", grouping=f"{5 + MAX_CHANNELS:02d}", min=1,
                     description="Put the results in this existing dataset..."),
        scripts.String("New_Dataset_Name", grouping=f"{6 + MAX_CHANNELS:02d}",
                       description="...or create a new dataset with this name. Give exactly one of the two."),
        scripts.Bool("Dry_Run", grouping=f"{7 + MAX_CHANNELS:02d}", default=False,
                     description="Check inputs and list planned outputs without writing anything."),
    ]
    return params


def run_script():
    client = scripts.client(
        "Rolling_Ball_Background.py",
        "Rolling-ball background subtraction (ImageJ port) on raw planes, one radius per channel. "
        "Set a radius to 0 to keep that channel unchanged; if all radii are 0 the image is copied. "
        "Results are new images named {basename}_bgs-{r1}-{r2}-....tif in one dataset; "
        "originals are not changed. Z_Projection optionally max-projects along Z before or after "
        "subtraction (_mip in the name marks where). " + SETTINGS + ". Tick Dry_Run to check the plan without writing.",
        *parameters(),
        version=VERSION, authors=["Jeff Lee Lab"], institutions=["Jeff Lee Lab"], namespaces=[NAMESPACE])
    try:
        params = client.getInputs(unwrap=True)
        conn = BlitzGateway(client_obj=client)
        message, outputs = run(conn, params)
        client.setOutput("Message", rstring(message))
        for key, obj in outputs.items():
            client.setOutput(key, robject(obj))
    finally:
        client.closeSession()


if __name__ == "__main__":
    run_script()
