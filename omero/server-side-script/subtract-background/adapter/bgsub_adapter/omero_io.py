"""Explicit OMERO image creation and metadata transfer (no scientific imports)."""
import hashlib
import json

import numpy as np
import omero.gateway
import omero.model as model
from omero.model.enums import UnitsLength, UnitsTime
from omero.rtypes import rint, rstring, unwrap
from omero.sys import ParametersI

from . import NAMESPACE
from .planning import ValidationError
from .runtime import SystemicError

PIXEL_FIELDS = {"PhysicalSizeX": "length", "PhysicalSizeY": "length",
                "PhysicalSizeZ": "length", "TimeIncrement": "time"}
LOGICAL_FIELDS = {"Name": "string", "Fluor": "string", "ExcitationWave": "length",
                  "EmissionWave": "length", "SamplesPerPixel": "int"}
PLANE_FIELDS = {"DeltaT": "time", "ExposureTime": "time", "PositionX": "length",
                "PositionY": "length", "PositionZ": "length"}
COLORS = ("Red", "Green", "Blue", "Alpha")


def encode(value):
    if value is None:
        return None
    if hasattr(value, "getUnit"):
        return {"value": value.getValue(), "unit": str(value.getUnit())}
    return unwrap(value)


def decode(value, kind):
    if value is None:
        return None
    if kind == "length":
        return model.LengthI(value["value"], getattr(UnitsLength, value["unit"]))
    if kind == "time":
        return model.TimeI(value["value"], getattr(UnitsTime, value["unit"]))
    return rstring(value) if kind == "string" else rint(value)


def fields(obj, mapping):
    return {key: encode(getattr(obj, "get" + key)()) for key in mapping}


def set_fields(obj, values, mapping):
    for key, kind in mapping.items():
        getattr(obj, "set" + key)(decode(values[key], kind))


def plane_hash(array):
    return hashlib.sha256(np.ascontiguousarray(array, dtype=array.dtype.newbyteorder("<")).tobytes()).hexdigest()


class OmeroIO:
    def __init__(self, conn, limits):
        self.conn, self.limits = conn, limits
        event = conn.getEventContext()
        self.user_id, self.group_id = event.userId, event.groupId
        conn.SERVICE_OPTS.setOmeroGroup(self.group_id)
        self.opts = conn.SERVICE_OPTS
        self.query, self.update = conn.getQueryService(), conn.getUpdateService()

    def get(self, kind, object_id, link=False):
        obj = self.conn.getObject(kind, object_id)
        if obj is None or obj.getDetails().getGroup().getId() != self.group_id:
            raise ValidationError(f"{kind} {object_id}: missing, inaccessible or outside active group")
        if link and not obj.canLink():
            raise ValidationError(f"Cannot link to {kind} {object_id}")
        return obj

    def query_all(self, hql, params):
        offset = 0
        while True:
            params.page(offset, 256)
            page = self.query.findAllByQuery(hql, params, self.opts)
            yield from page
            if len(page) < 256:
                return
            offset += len(page)

    def select(self, p):
        if p["Data_Type"] == "Image":
            if len(p["IDs"]) > self.limits["max_images_per_batch"]:
                raise ValidationError("Image selection exceeds configured batch limit")
            return [self.get("Image", i) for i in p["IDs"]]
        self.get("Dataset", p["IDs"][0])
        params = ParametersI().addId(p["IDs"][0])
        hql = "select l from DatasetImageLink l where l.parent.id=:id order by l.id"
        # Resolve all children; filtering is later, before collecting detailed metadata.
        return (self.get("Image", link.getChild().getId().val)
                for link in self.query_all(hql, params))

    def snapshot(self, image):
        sizes = {axis.lower(): getattr(image, "getSize" + axis)() for axis in "XYZCT"}
        if sizes["z"] * sizes["c"] * sizes["t"] > self.limits["max_planes_per_batch"]:
            raise ValidationError(f"Image {image.getId()} exceeds plane-count limit")
        pixels = image.getPrimaryPixels()
        channels = image.getChannels(noRE=True)
        metadata = {"pixels": fields(pixels._obj, PIXEL_FIELDS), "channels": [], "planes": []}
        for channel in channels:
            lc = channel._obj.getLogicalChannel()
            data = fields(lc, LOGICAL_FIELDS)
            data["color"] = fields(channel._obj, COLORS)
            metadata["channels"].append(data)
        params = ParametersI().addId(pixels.getId())
        hql = "select p from PlaneInfo p where p.pixels.id=:id order by p.id"
        seen = set()
        for pi in self.query_all(hql, params):
            coords = [encode(pi.getTheZ()), encode(pi.getTheC()), encode(pi.getTheT())]
            if tuple(coords) in seen:
                raise ValidationError("Duplicate source PlaneInfo coordinates")
            seen.add(tuple(coords))
            metadata["planes"].append({"zct": coords, "fields": fields(pi, PLANE_FIELDS)})
        metadata["planes"].sort(key=lambda pi: pi["zct"])
        # Read rendering settings without creating/saving source rendering defaults.
        hql = ("select r from RenderingDef r where r.pixels.id=:id "
               "order by r.id")
        rdefs = list(self.query_all(hql, ParametersI().addId(pixels.getId())))
        preferred = sorted(rdefs, key=lambda r: (r.getDetails().getOwner().getId().val != self.user_id,
                                                 r.getDetails().getOwner().getId().val != image.getOwner().getId(),
                                                 r.getId().val))
        metadata["rendering"] = None
        if preferred:
            params = ParametersI().addId(preferred[0].getId().val)
            rd = self.query.findByQuery(
                "select distinct r from RenderingDef r left join fetch r.waveRendering where r.id=:id",
                params, self.opts)
            metadata["rendering"] = [{"active": encode(b.getActive()), "color": fields(b, COLORS)}
                                     for b in rd.copyWaveRendering()]
            if len(metadata["rendering"]) != sizes["c"]:
                raise ValidationError("Source rendering channel count is inconsistent")
        plate = bool(self.query.projection(
            "select w.id from WellSample w where w.image.id=:id",
            ParametersI().addId(image.getId()), self.opts))
        dtype = pixels.getPixelsType().getValue()
        return {"id": image.getId(), "pixels_id": pixels.getId(), "name": image.getName(),
                "group_id": self.group_id, **sizes, "dtype": {"float": "float32"}.get(dtype, dtype),
                "channel_names": [ch["Name"] for ch in metadata["channels"]],
                "parent_dataset_ids": sorted(parent.getId() for parent in image.listParents()),
                "plate": plate,
                "multisample": any(ch["SamplesPerPixel"] not in (None, 1) for ch in metadata["channels"]),
                "metadata": metadata}

    def validate_destination(self, p):
        if self.conn.canCreate() is not True:
            raise ValidationError("Server is read-only or its writable status is indeterminate")
        if p["Output_Mode"] == "Existing_Dataset":
            dataset = self.get("Dataset", p["Output_Dataset_ID"], link=True)
            if not dataset.canAnnotate():
                raise ValidationError("Output dataset must allow annotations/reports")
        elif p["Output_Project_ID"] is not None:
            self.get("Project", p["Output_Project_ID"], link=True)

    def find_marker(self, kind, marker):
        params = ParametersI().addString("marker", marker).addLong("owner", self.user_id)
        found = self.query.findAllByQuery(
            f"select o from {kind} o where o.description=:marker and o.details.owner.id=:owner",
            params, self.opts)
        if len(found) > 1:
            raise SystemicError("Ambiguous creation marker; operator reconciliation required")
        return found[0].getId().val if found else None

    def destination(self, p, journal):
        record = journal.data
        if record.get("destination_id") is not None:
            self.get("Dataset", record["destination_id"], link=True)
        elif p["Output_Mode"] == "Existing_Dataset":
            record["destination_id"] = p["Output_Dataset_ID"]
            journal.save()
        else:
            marker = "OMERO bgsub dataset; run=" + record["run_id"]
            if record.get("dataset_create_intent"):
                found = self.find_marker("Dataset", marker)
                if found is None:
                    raise SystemicError("Uncertain dataset creation. Operator must reconcile before retrying; see INSTALL.md")
            else:
                record["dataset_create_intent"] = True
                journal.save()
                ds = model.DatasetI()
                ds.setName(rstring(p["New_Dataset_Name"]))
                ds.setDescription(rstring(marker))
                found = self.update.saveAndReturnObject(ds, self.opts).getId().val
            record["destination_id"] = found
            journal.save()
        if p["Output_Mode"] == "New_Dataset" and p["Output_Project_ID"] is not None:
            params = ParametersI().addLong("p", p["Output_Project_ID"]).addLong("d", record["destination_id"])
            links = self.query.projection(
                "select l.id from ProjectDatasetLink l where l.parent.id=:p and l.child.id=:d", params, self.opts)
            if not links:
                link = model.ProjectDatasetLinkI()
                link.setParent(model.ProjectI(p["Output_Project_ID"], False))
                link.setChild(model.DatasetI(record["destination_id"], False))
                self.update.saveObject(link, self.opts)
        return record["destination_id"]

    def ensure_output(self, row, journal):
        marker = f"OMERO bgsub image; run={journal.data['run_id']}; source={row['source']['id']}"
        if row.get("output_id") is None:
            if row.get("create_intent"):
                found = self.find_marker("Image", marker)
                if found is None:
                    raise SystemicError("Uncertain image creation; operator reconciliation required before retry")
            else:
                row["create_intent"] = True
                journal.save()
                s = row["source"]
                pt = "float" if s["dtype"] == "float32" else s["dtype"]
                pixel_type = self.query.findByQuery("from PixelsType p where p.value=:type",
                                                   ParametersI().addString("type", pt), self.opts)
                found = self.conn.getPixelsService().createImage(
                    s["x"], s["y"], s["z"], s["t"], list(range(s["c"])), pixel_type,
                    row["output_name"], marker, self.opts).val
            row["output_id"] = found
            journal.save()  # before allocating/uploading any output plane
        output = self.get("Image", row["output_id"])
        if output.getOwner().getId() != self.user_id or output.getDescription() != marker:
            raise SystemicError("Output ownership/run marker mismatch")
        if output.getName() != row["output_name"]:
            raise SystemicError("Recorded output was renamed; operator reconciliation required")
        self.verify_geometry(output, row["source"])
        return output

    def verify_geometry(self, output, source):
        for axis in "XYZCT":
            if getattr(output, "getSize" + axis)() != source[axis.lower()]:
                raise SystemicError("Output geometry mismatch: " + axis)
        stored = output.getPrimaryPixels().getPixelsType().getValue()
        if {"float": "float32"}.get(stored, stored) != source["dtype"]:
            raise SystemicError("Output dtype mismatch")

    def copy_metadata(self, output, source):
        meta = source["metadata"]
        pixels = output.getPrimaryPixels()._obj
        set_fields(pixels, meta["pixels"], PIXEL_FIELDS)
        self.update.saveObject(pixels, self.opts)
        for channel, values in zip(output.getChannels(noRE=True), meta["channels"]):
            logical = channel._obj.getLogicalChannel()
            set_fields(logical, values, LOGICAL_FIELDS)
            self.update.saveObject(logical, self.opts)
            for field in COLORS:
                getattr(channel._obj, "set" + field)(decode(values["color"][field], "int"))
            channel.save()
        params = ParametersI().addId(output.getPixelsId())
        existing = {}
        for pi in self.query_all("select p from PlaneInfo p where p.pixels.id=:id order by p.id", params):
            coords = tuple(encode(getattr(pi, "getThe" + axis)()) for axis in "ZCT")
            if coords in existing:
                raise SystemicError("Duplicate output plane metadata; manual reconciliation required")
            existing[coords] = pi
        for info in meta["planes"]:
            coords = tuple(info["zct"])
            pi = existing.get(coords, model.PlaneInfoI())
            pi.setPixels(model.PixelsI(output.getPixelsId(), False))
            for axis, value in zip("ZCT", coords):
                getattr(pi, "setThe" + axis)(rint(value))
            set_fields(pi, info["fields"], PLANE_FIELDS)
            self.update.saveObject(pi, self.opts)

    def rendering(self, output, source, ranges):
        # set_active_channels uses 1-based signed indices internally, unlike the user form.
        render = source["metadata"]["rendering"]
        active, colors, windows = [], [], []
        for c in range(source["c"]):
            info = render[c] if render else {"active": True, "color": source["metadata"]["channels"][c]["color"]}
            active.append(c + 1 if info["active"] else -(c + 1))
            rgba = [info["color"][name] for name in COLORS]
            colors.append("".join(f"{n:02X}" for n in rgba) if all(n is not None for n in rgba) else None)
            low, high = ranges[c]
            windows.append([low, high if high > low else low + 1])
        output.set_active_channels(active, windows=windows, colors=colors, set_inactive=True)
        if not output.saveDefaults():
            raise SystemicError("Cannot save output rendering settings")

    def provenance(self, output, payload):
        found = [a for a in output.listAnnotations(ns=NAMESPACE) if a.getOwner().getId() == self.user_id]
        if len(found) > 1:
            raise SystemicError("Duplicate provenance annotations; reconcile before resume")
        ann = found[0] if found else omero.gateway.CommentAnnotationWrapper(self.conn)
        ann.setNs(NAMESPACE)
        ann.setValue(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False))
        ann.save()
        if not found:
            output.linkAnnotation(ann)

    def link_output(self, output, dataset_id):
        params = ParametersI().addLong("d", dataset_id).addLong("i", output.getId())
        hql = "select l.id from DatasetImageLink l where l.parent.id=:d and l.child.id=:i"
        if not self.query.projection(hql, params, self.opts):
            link = model.DatasetImageLinkI()
            link.setParent(model.DatasetI(dataset_id, False))
            link.setChild(model.ImageI(output.getId(), False))
            self.update.saveObject(link, self.opts)
        if not self.query.projection(hql, params, self.opts):
            raise SystemicError("Destination link could not be verified")

    def verify_complete(self, row, journal):
        output = self.ensure_output(row, journal)
        found = [a for a in output.listAnnotations(ns=NAMESPACE) if a.getOwner().getId() == self.user_id]
        if len(found) != 1:
            raise SystemicError("Recorded complete output lacks unique provenance")
        payload = json.loads(found[0].getValue())
        if payload.get("state") != "COMPLETE" or payload.get("fingerprint") != row["fingerprint"]:
            raise SystemicError("Recorded complete output has changed provenance")
        if journal.data["destination_id"] not in [d.getId() for d in output.listParents()]:
            raise SystemicError("Recorded complete output is no longer in destination")
        self.verify_pixels(output, row["plane_hashes"])
        self.verify_metadata(output, row["source"])

    def verify_pixels(self, output, hashes):
        pixels = output.getPrimaryPixels()
        for key, expected in hashes.items():
            z, c, t = map(int, key.split(","))
            if plane_hash(pixels.getPlane(z, c, t)) != expected:
                raise SystemicError(f"Read-back differs at Z/C/T={key}")

    def verify_metadata(self, output, source):
        observed = self.snapshot(output)["metadata"]
        expected = source["metadata"]
        for key in ("pixels", "channels", "planes"):
            if observed[key] != expected[key]:
                raise SystemicError("Output metadata differs: " + key)
        if expected["rendering"] is not None and observed["rendering"] != expected["rendering"]:
            raise SystemicError("Output channel colours/active choices differ")
