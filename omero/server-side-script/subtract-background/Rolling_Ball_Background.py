#!/usr/bin/env python
"""Rolling-ball background subtraction for OMERO images.

Reads raw planes, applies the lab's ImageJ rolling-ball port (misc/python-rolling-ball) with one
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

VERSION = "0.4.2"
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
ALGORITHM_SOURCE = "misc/python-rolling-ball/imagej_rolling_ball.py"
SETTINGS = ("presmooth on; automatic shrink; dark background; "
            "corrected negatives clipped to 0; source pixel type kept")
SUPPORTED_TYPES = ("uint8", "uint16", "float")
RADIUS_HELP = "Radius in XY pixels. 0 = skip subtraction and keep this channel unchanged."
PROJECTIONS = ("Off", "Max projection, then subtract", "Subtract, then max projection")
PROJECTION_OFF, MIP_FIRST, MIP_LAST = PROJECTIONS


# Rolling-ball algorithm: the functions from here to the end marker are copied from
# https://github.com/JeffLeeLab/Image-analysis/tree/main/misc/python-rolling-ball (imagej_rolling_ball.py),
# a port of ImageJ's BackgroundSubtracter.java checked against ImageJ. They are kept inline because
# OMERO server-side scripts must be a single file. Fix them in the original and copy them here again;
# do not edit them here. test_local.py checks that each one matches the original.
# --- BEGIN copied from misc/python-rolling-ball/imagej_rolling_ball.py ---
# ImageJ's RollingBall constructor: (largest radius, shrink factor, arc-trim percent)
_IMAGEJ_BALL_TABLE = (
    (10, 1, 24),
    (30, 2, 24),
    (100, 4, 32),
    (np.inf, 8, 40),
)


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
# --- END copied from misc/python-rolling-ball/imagej_rolling_ball.py ---


def subtract_background_image(image: np.ndarray, background) -> tuple[np.ndarray, float]:
    """``image - background``, negatives clipped to 0 (spec.md). Returns (float64 image, frac_clipped)."""
    corrected = np.asarray(image, dtype=np.float64) - background
    if corrected.size == 0:
        return corrected, float("nan")
    frac_clipped = float(np.mean(corrected < 0))
    return np.clip(corrected, 0, None), frac_clipped


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
        ["algorithm_source", ALGORITHM_SOURCE],
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
