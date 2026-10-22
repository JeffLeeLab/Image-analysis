#!/usr/bin/env python
"""Z-projection (max, sum or average intensity) for OMERO images.

Reads raw planes, projects each channel and timepoint along Z (all planes, or an
optional 1-based Z range), and writes new images into one explicitly chosen
dataset. Source images are never modified.
Needs only NumPy, which OMERO itself uses (see ../README.md).
"""
from __future__ import annotations

import datetime
import fnmatch

import numpy as np
import omero
import omero.scripts as scripts
from omero.gateway import BlitzGateway, DatasetWrapper, MapAnnotationWrapper
from omero.rtypes import rint, rlong, robject, rstring

VERSION = "0.2.0"
MAX_NAME_LENGTH = 255
MAX_MESSAGE_LINES = 50
NAMESPACE = "org.jeffleelab.omero.zproject"
SUPPORTED_TYPES = ("uint8", "uint16", "float")
METHODS = ("Max intensity", "Sum intensity", "Average intensity")
MAX_METHOD, SUM_METHOD, AVG_METHOD = METHODS
SUFFIXES = {MAX_METHOD: "_mip", SUM_METHOD: "_sum", AVG_METHOD: "_avg"}
# OMERO has no 64-bit integer pixel type, so integer sums are stored as uint32.
NUMPY_TYPES = {"uint8": np.uint8, "uint16": np.uint16, "uint32": np.uint32, "float": np.float32}


def requested_z(params):
    """(Z_Start, Z_End) as given: 1-based and inclusive, None where blank."""
    start, end = params.get("Z_Start"), params.get("Z_End")
    for field, value in (("Z_Start", start), ("Z_End", end)):
        if value is not None and value < 1:
            raise ValueError(f"{field} must be 1 or more (Z-planes are numbered from 1)")
    if start is not None and end is not None and start > end:
        raise ValueError(f"Z_Start ({start}) is after Z_End ({end})")
    return start, end


def z_text(requested):
    """The requested Z-planes for messages: all, 3-15, 5-last or 1-15."""
    start, end = requested
    if start is None and end is None:
        return "all"
    return f"{start or 1}-{end or 'last'}"


def image_z_range(requested, size_z):
    """None for the whole stack, else this image's 1-based inclusive (start, end).

    A blank end is open: Z_Start alone runs to the image's last plane, Z_End alone starts at plane 1.
    """
    start, end = requested
    if start is None and end is None:
        return None
    if (start or 1) > size_z or (end or 1) > size_z:
        raise ValueError(f"Z-planes {z_text(requested)} do not fit its {size_z} Z-planes")
    return start or 1, end or size_z


def z_indices(z_range, size_z):
    """0-based source planes to project."""
    return range(size_z) if z_range is None else range(z_range[0] - 1, z_range[1])


def output_name(name, method, z_range=None):
    """Output name: {basename}_mip.tif, _sum.tif or _avg.tif, plus the Z range if given (_mip3-15.tif)."""
    base = name
    for suffix in (".ome.tiff", ".ome.tif", ".tiff", ".tif"):
        if name.lower().endswith(suffix):
            base = name[:-len(suffix)]
            break
    else:
        dot = name.rfind(".")
        if 0 < dot < len(name) - 1:
            base = name[:dot]
    if not base:
        raise ValueError(f"Image name '{name}' has an empty basename")
    span = "" if z_range is None else f"{z_range[0]}-{z_range[1]}"
    result = base + SUFFIXES[method] + span + ".tif"
    if len(result) > MAX_NAME_LENGTH:
        raise ValueError(f"Output name for '{name}' exceeds {MAX_NAME_LENGTH} characters")
    return result


def output_pixel_type(source_type, method):
    """Max keeps the source type; Sum of integers is uint32; Average is always float (32-bit)."""
    if method == AVG_METHOD:
        return "float"
    if method == SUM_METHOD and source_type != "float":
        return "uint32"
    return source_type


def project(planes, method, out_type):
    """Project 2D planes along Z. Sums use uint64 or float64, then must fit out_type."""
    result, count = None, 0
    for plane in planes:
        if plane.dtype.kind == "f" and not np.isfinite(plane).all():
            raise ValueError("Plane contains NaN or infinite values")
        if result is None:
            wide = plane.dtype if method == MAX_METHOD else (np.uint64 if plane.dtype.kind == "u" else np.float64)
            result = plane.astype(wide)
        elif method == MAX_METHOD:
            np.maximum(result, plane, out=result)
        else:
            result += plane
        count += 1
    if result is None:
        raise ValueError("No planes to project")
    if method == AVG_METHOD:
        result = result / count
    out_dtype = np.dtype(NUMPY_TYPES[out_type])
    if out_dtype.kind == "u" and result.max() > np.iinfo(out_dtype).max:
        raise ValueError(f"Projected values exceed the {out_type} range")
    if out_dtype.kind == "f" and np.abs(result).max() > np.finfo(out_dtype).max:
        raise ValueError("Projected values are not representable as float32")
    return result.astype(out_dtype, copy=False)


def output_planes(get_plane, z_planes, size_c, size_t, method, out_type):
    """Yield (z, c, t, plane) for the output, which has one z-plane. z_planes are 0-based source planes."""
    for c in range(size_c):
        for t in range(size_t):
            yield 0, c, t, project((get_plane(z, c, t) for z in z_planes), method, out_type)


def pixel_type(image):
    return image.getPrimaryPixels().getPixelsType().getValue()


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


def plan_batch(images, method, requested=(None, None)):
    """Check the whole batch before any write. A Z range that does not fit any one image stops the batch.

    Returns ([(image, output name, output pixel type, image Z range)], [skipped single-Z image labels]).
    """
    errors, plan, skipped = [], [], []
    for image in images:
        label = f"{image.getId()} {image.getName()}"
        if image.getSizeZ() < 2:
            skipped.append(label)
            continue
        try:
            z_range = image_z_range(requested, image.getSizeZ())
        except ValueError as exc:
            errors.append(f"Image {label}: {exc}")
            continue
        source_type = pixel_type(image)
        if source_type not in SUPPORTED_TYPES:
            errors.append(f"Image {label}: pixel type {source_type} is not supported (uint8, uint16, float)")
            continue
        if needs_pyramid(image):
            errors.append(f"Image {label}: too large for whole-plane processing")
            continue
        try:
            plan.append((image, output_name(image.getName(), method, z_range),
                         output_pixel_type(source_type, method), z_range))
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError("\n".join(errors))
    return plan, skipped


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


def add_provenance(conn, new_image, source, method, out_type, z_range):
    planes = z_indices(z_range, source.getSizeZ())
    annotation = MapAnnotationWrapper(conn)
    annotation.setNs(NAMESPACE)
    annotation.setValue([
        ["source_image_id", str(source.getId())],
        ["source_image_name", source.getName()],
        ["projection", method],
        ["z_start", str(planes.start + 1)],
        ["z_end", str(planes.stop)],
        ["z_planes", str(len(planes))],
        ["source_size_z", str(source.getSizeZ())],
        ["source_pixel_type", pixel_type(source)],
        ["output_pixel_type", out_type],
        ["script_version", VERSION],
        ["created_utc", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")],
    ])
    annotation.save()
    new_image.linkAnnotation(annotation)


def process_image(conn, image, method, name, out_type, z_range, dataset):
    """Write one Z-projection of image into dataset, over z_range (None for the whole stack)."""
    pixels_service = conn.getPixelsService()
    update = conn.getUpdateService()
    size_z, size_c, size_t = image.getSizeZ(), image.getSizeC(), image.getSizeT()
    source_type = pixel_type(image)
    span = f"{size_z} Z-planes" if z_range is None else f"Z-planes {z_range[0]}-{z_range[1]} of {size_z}"
    # Copies dimensions, pixel type, physical sizes and channel metadata, not pixels.
    new_id = pixels_service.copyAndResizeImage(
        image.getId(), rint(image.getSizeX()), rint(image.getSizeY()), rint(1), rint(size_t),
        list(range(size_c)), None, False, conn.SERVICE_OPTS).getValue()
    try:
        new_image = conn.getObject("Image", new_id)
        new_image._obj.setName(rstring(name))
        new_image._obj.setDescription(rstring(
            f"{method} Z-projection of image {image.getId()} ({image.getName()}) over {span}. "
            f"Pixel type {source_type} -> {out_type}."))
        update.saveObject(new_image._obj, conn.SERVICE_OPTS)
        if out_type != source_type:
            # Change the type before any pixels are written, as OMERO's own projection service does.
            query = conn.getQueryService()
            pixels = query.get("Pixels", new_image.getPixelsId(), conn.SERVICE_OPTS)
            pixels.setPixelsType(query.findByString("PixelsType", "value", out_type, conn.SERVICE_OPTS))
            update.saveObject(pixels, conn.SERVICE_OPTS)
        ranges = [[np.inf, -np.inf] for _ in range(size_c)]
        store = conn.c.sf.createRawPixelsStore()
        try:
            store.setPixelsId(new_image.getPixelsId(), True, conn.SERVICE_OPTS)
            for z, c, t, result in output_planes(image.getPrimaryPixels().getPlane, z_indices(z_range, size_z),
                                                 size_c, size_t, method, out_type):
                wire = np.ascontiguousarray(result, dtype=result.dtype.newbyteorder(">"))
                store.setPlane(wire.tobytes(), z, c, t, conn.SERVICE_OPTS)
                ranges[c] = [min(ranges[c][0], float(result.min())),
                             max(ranges[c][1], float(result.max()))]
        finally:
            store.close(conn.SERVICE_OPTS)
        for c, (low, high) in enumerate(ranges):
            pixels_service.setChannelGlobalMinMax(new_image.getPixelsId(), c, low, high, conn.SERVICE_OPTS)
        add_provenance(conn, new_image, image, method, out_type, z_range)
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


def skipped_lines(skipped):
    return ["Skipped (only one Z-plane):"] + limited(skipped) if skipped else []


def run(conn, params):
    """Returns (message, {output name: OMERO model object})."""
    method = params.get("Projection", MAX_METHOD)
    try:
        requested = requested_z(params)
        images = collect_images(conn, params)
        if not images:
            return "No images matched the selection and name filter. Nothing was written.", {}
        plan, skipped = plan_batch(images, method, requested)
        if not plan:
            return "\n".join(["No image has more than one Z-plane. Nothing was written."]
                             + skipped_lines(skipped)), {}
        dataset, new_name = resolve_destination(conn, params)
    except ValueError as exc:
        return "Nothing was written.\n" + str(exc), {}
    if params.get("Dry_Run", False):
        target = f"dataset {dataset.getId()} '{dataset.getName()}'" if dataset else f"new dataset '{new_name}'"
        lines = [f"Dry run: {len(plan)} image(s) -> {target}.", f"Projection: {method}.",
                 f"Z-planes: {z_text(requested)}."]
        lines += limited([f"{image.getId()} {image.getName()} -> {name} ({out_type})"
                          for image, name, out_type, _ in plan])
        return "\n".join(lines + skipped_lines(skipped) + ["Untick Dry_Run to process."]), {}
    if dataset is None:
        dataset = DatasetWrapper(conn, obj=omero.model.DatasetI())
        dataset.setName(rstring(new_name))
        dataset.save()
    done, failed = [], []
    for index, (image, name, out_type, z_range) in enumerate(plan, start=1):
        print(f"Image {index}/{len(plan)}: {image.getId()} {image.getName()}", flush=True)
        try:
            done.append(process_image(conn, image, method, name, out_type, z_range, dataset))
        except Exception as exc:
            failed.append(f"{image.getId()} {image.getName()}: {type(exc).__name__}: {exc}")
            print(failed[-1], flush=True)
    lines = [f"{len(done)} of {len(plan)} image(s) written to dataset {dataset.getId()} '{dataset.getName()}'.",
             f"Projection: {method}.", f"Z-planes: {z_text(requested)}."]
    if failed:
        lines += ["Failed (no output kept):"] + limited(failed)
    lines += skipped_lines(skipped)
    outputs = {"Dataset": dataset._obj}
    if len(done) == 1:
        outputs["Image"] = done[0]._obj
    return "\n".join(lines), outputs


def parameters():
    return [
        scripts.String("Data_Type", optional=False, grouping="01", default="Image",
                       values=[rstring("Image"), rstring("Dataset")],
                       description="Process the selected images, or every image in one dataset."),
        scripts.List("IDs", optional=False, grouping="02",
                     description="Image IDs, or exactly one dataset ID.").ofType(rlong(0)),
        scripts.String("Name_Filter", grouping="03",
                       description="Optional wildcard on the full image name (* and ?), e.g. *.tif. "
                                   "Blank processes all."),
        scripts.String("Projection", grouping="04", default=MAX_METHOD,
                       values=[rstring(m) for m in METHODS],
                       description="Max keeps the pixel type. Sum gives uint32 (float stays float). "
                                   "Average gives float."),
        scripts.Int("Z_Start", grouping="05", min=1,
                    description="Optional first Z-plane to project (1-based). Blank starts at plane 1. "
                                "Leave both Z fields blank to project the whole stack."),
        scripts.Int("Z_End", grouping="06", min=1,
                    description="Optional last Z-plane to project (included). Blank ends at each image's "
                                "last plane. A range that does not fit every image stops the run."),
        scripts.Long("Output_Dataset_ID", grouping="07", min=1,
                     description="Put the results in this existing dataset..."),
        scripts.String("New_Dataset_Name", grouping="08",
                       description="...or create a new dataset with this name. Give exactly one of the two."),
        scripts.Bool("Dry_Run", grouping="09", default=False,
                     description="Check inputs and list planned outputs without writing anything."),
    ]


def run_script():
    client = scripts.client(
        "Z_Projection.py",
        "Projects each image along Z (max, sum or average intensity) for every channel and timepoint. "
        "Results are new images named {basename}_mip.tif, _sum.tif or _avg.tif in one dataset; "
        "originals are not changed. Z_Start/Z_End optionally limit the Z-planes (1-based), "
        "e.g. {basename}_mip3-15.tif. Max keeps the pixel type; Sum gives uint32 (float stays float); "
        "Average gives float. Images with one Z-plane are skipped.",
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
