"""Focused checks for the new adapter/protocol. No ImageJ parity suite or live server."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapter"))
from bgsub_adapter.planning import (parse_inputs, output_name, radius_token, make_plan,
                                    matches, destination_errors, ValidationError)
from bgsub_adapter.runtime import Worker, Journal, admission, SystemicError


def limits():
    return json.loads((ROOT / "config/operator.example.json").read_text())


def config(**values):
    data = {"IDs": [7], "Data_Type": "Image", "Output_Dataset_ID": 19,
            "Channel_Match_Mode": "Index", "Rule_1_Channel": "0", "Rule_1_Radius": 0}
    data.update(values)
    return parse_inputs(data, limits())


def source():
    return {"id": 7, "name": "sample.ome.tif", "x": 3, "y": 2, "z": 2, "c": 1,
            "t": 2, "dtype": "uint16", "channel_names": ["DAPI"], "metadata": {}, "group_id": 2}


class PlanningChecks(unittest.TestCase):
    def test_zero_and_missing_are_different(self):
        self.assertEqual(config()["rules"], {"0": "0"})
        with self.assertRaises(ValidationError):
            config(Rule_1_Radius=None)

    def test_dataset_cardinality_and_image_deduplication(self):
        with self.assertRaises(ValidationError):
            config(Data_Type="Dataset", IDs=[1, 1])
        self.assertEqual(config(IDs=[7, 8, 7])["IDs"], [7, 8])

    def test_strict_layout_with_variable_dimensions(self):
        a, b = source(), source()
        b.update(id=8, x=5, z=3)
        self.assertFalse(make_plan([a, b], config(), limits())["errors"])
        b["channel_names"] = ["GFP"]
        self.assertTrue(make_plan([a, b], config(), limits())["errors"])

    def test_naming(self):
        self.assertEqual(output_name("field.001.OME.TIFF", [0, 12.50, 1e2], 255),
                         "field.001_bgs-0-12.5-100.tif")
        self.assertEqual(radius_token(-0.0), "0")
        self.assertEqual(radius_token(1e-5), "0.00001")
        with self.assertRaises(ValidationError):
            output_name("x.tif", [0, 50], 10)

    def test_wildcard_only_star_and_question_are_special(self):
        p = config(Name_Filter="[A]*?", Name_Match_Mode="Wildcard")
        self.assertTrue(matches("[A]file", p))
        self.assertFalse(matches("Afile", p))

    def test_explicit_destination(self):
        p = config(Output_Mode="New_Dataset", Output_Dataset_ID=None, New_Dataset_Name="Result")
        self.assertFalse(destination_errors(p, 255))
        p["Output_Dataset_ID"] = 123
        self.assertTrue(destination_errors(p, 255))

    def test_inventory_is_not_processing_ready(self):
        p = config(Rule_1_Channel=None, Rule_1_Radius=None)
        self.assertTrue(make_plan([source()], p, limits())["errors"])

    def test_journal_and_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory, "a" * 32, {"state": "started"})
            journal.data["state"] = "done"
            journal.save()
            self.assertEqual(Journal(directory, "a" * 32).data["state"], "done")
            with admission(directory):
                with self.assertRaises(SystemicError):
                    with admission(directory):
                        pass

    def test_form_has_optional_zero_without_default(self):
        spec = importlib.util.spec_from_file_location("bgsub_entry", ROOT / "scripts/Batch_Rolling_Ball_Background.py")
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        p = {value.name(): value for value in entry.parameters()}
        self.assertEqual(len(p), 28)
        self.assertTrue(p["Rule_1_Radius"].optional)
        self.assertFalse(p["Rule_1_Radius"].useDefault)
        self.assertIn("0", p["Rule_1_Radius"].description)

    def test_model_metadata_units_roundtrip(self):
        from bgsub_adapter.omero_io import encode, decode
        for kind, unit in (("length", "MICROMETER"), ("time", "SECOND")):
            original = {"value": 0.5, "unit": unit}
            self.assertEqual(encode(decode(original, kind)), original)

    def test_uncertain_creation_does_not_duplicate(self):
        from bgsub_adapter.omero_io import OmeroIO
        io = OmeroIO.__new__(OmeroIO)
        io.find_marker = lambda *args: None
        row = {"source": source(), "create_intent": True, "output_id": None}
        journal = SimpleNamespace(data={"run_id": "a" * 32})
        with self.assertRaises(SystemicError):
            io.ensure_output(row, journal)

    def test_fatal_processing_error_can_resume_without_poisoning_preflight(self):
        from bgsub_adapter import application
        with tempfile.TemporaryDirectory() as directory:
            cfg = limits()
            cfg.update(journal_root=directory, temporary_root=directory, worker_python=sys.executable)
            path = Path(directory) / "operator.json"
            path.write_text(json.dumps(cfg))
            values = {"IDs": [7], "Run_Mode": "Process", "Output_Dataset_ID": 19,
                      "Channel_Match_Mode": "Index", "Rule_1_Channel": "0", "Rule_1_Radius": 0}
            client = SimpleNamespace(getInputs=lambda **kwargs: values)
            image = SimpleNamespace(getId=lambda: 7, getName=lambda: "sample.ome.tif")
            io = SimpleNamespace(user_id=1, group_id=2, select=lambda p: [image],
                                 get=lambda *args: image, snapshot=lambda image: source(),
                                 validate_destination=lambda p: None)
            def destination(p, journal):
                journal.data["destination_id"] = 19
                journal.save()
            io.destination = destination
            reports = []
            def report(client, io, journal, cfg):
                reports.append(copy.deepcopy(journal.data))
            with patch.object(application, "OmeroIO", return_value=io), \
                    patch.object(application, "limit_wrapper_memory"), \
                    patch.object(application, "report", side_effect=report):
                with patch.object(application, "process_image", side_effect=SystemicError("simulated timeout")):
                    application.execute(client, None, str(path))
                self.assertEqual(reports[-1]["status"], "FAILED")
                self.assertEqual(reports[-1]["plan"]["errors"], [])
                values["Resume_Run_ID"] = reports[-1]["run_id"]
                def finish(io, row, journal, cfg, deadline):
                    row["state"] = "COMPLETE"
                with patch.object(application, "process_image", side_effect=finish):
                    application.execute(client, None, str(path))
                self.assertEqual(reports[-1]["status"], "SUCCESS")
                self.assertEqual(reports[-1]["run_id"], values["Resume_Run_ID"])
                self.assertNotIn("fatal_error", reports[-1])
                with patch.object(application, "process_image", side_effect=ValidationError("completed output changed")):
                    application.execute(client, None, str(path))
                self.assertEqual(reports[-1]["status"], "PARTIAL_FAILURE")
                self.assertIn("completed output changed", reports[-1]["fatal_error"])
                self.assertEqual(reports[-1]["plan"]["rows"][0]["state"], "COMPLETE")


@unittest.skipUnless(os.environ.get("BGSUB_WORKER_PYTHON"), "Set BGSUB_WORKER_PYTHON to the installed isolated worker")
class WorkerChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = limits()
        self.cfg.update(worker_python=os.environ["BGSUB_WORKER_PYTHON"], temporary_root=self.temp.name,
                        min_temporary_free_bytes=1, worker_timeout_seconds=15)

    def tearDown(self):
        self.temp.cleanup()

    def test_real_process_copy_and_positive_radius(self):
        import numpy as np
        with Worker(self.cfg, time.monotonic() + 60) as worker:
            negative = np.array([[-3.5, 0, 1], [2, 3, 4]], dtype=np.float32)
            out, stats = worker.plane(negative, "0")
            np.testing.assert_array_equal(negative, out)
            self.assertEqual(stats["clipped_pixels"], 0)
            # Protocol/dtype exercise, not a second scientific parity test.
            out, stats = worker.plane(np.full((16, 16), 20, dtype=np.uint16), "5")
            self.assertEqual(out.dtype, np.dtype("uint16"))
            self.assertEqual(out.shape, (16, 16))
            self.assertEqual(stats["pixels"], 256)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_nonfinite_bypassed_pixels_rejected(self):
        import numpy as np
        with Worker(self.cfg, time.monotonic() + 60) as worker:
            with self.assertRaises(ValueError):
                worker.plane(np.array([[np.nan]], dtype=np.float32), "0")

    def test_zct_raw_upload_and_resume(self):
        import numpy as np
        from bgsub_adapter.application import process_image
        from bgsub_adapter.omero_io import plane_hash
        s = source()
        input_planes = {(z, 0, t): np.arange(6, dtype=np.uint16).reshape(2, 3) + z * 100 + t * 10
                        for z in range(2) for t in range(2)}
        uploaded, calls = {}, []
        class Store:
            def setPixelsId(self, *args):
                pass
            def setPlane(self, data, z, c, t, opts):
                calls.append((z, c, t))
                uploaded[z, c, t] = np.frombuffer(data, dtype=">u2").astype("uint16").reshape(2, 3)
            def close(self, *args):
                pass
        class Image:
            def getPrimaryPixels(self):
                return SimpleNamespace(getPlane=lambda z,c,t: input_planes[z,c,t])
            def getPixelsId(self):
                return 71
            def getId(self):
                return 70
            def listParents(self):
                return []
        image = Image()
        io = SimpleNamespace(
            user_id=1, opts={}, snapshot=lambda obj: copy.deepcopy(s), get=lambda *args: image,
            ensure_output=lambda *args: image, provenance=lambda *args: None,
            copy_metadata=lambda *args: None, rendering=lambda *args: None,
            verify_geometry=lambda *args: None, verify_metadata=lambda *args: None,
            link_output=lambda *args: None, verify_complete=lambda *args: calls.append("resume_verified"),
            conn=SimpleNamespace(c=SimpleNamespace(sf=SimpleNamespace(createRawPixelsStore=Store)),
                                 getPixelsService=lambda: SimpleNamespace(setChannelGlobalMinMax=lambda *args: None)))
        def verify_pixels(obj, hashes):
            for key, checksum in hashes.items():
                self.assertEqual(plane_hash(uploaded[tuple(map(int,key.split(',')))]), checksum)
        io.verify_pixels = verify_pixels
        journal = Journal(self.temp.name, "b" * 32, {"run_id": "b" * 32, "destination_id": 19,
                                                     "wrapper": {}, "started_at": "now"})
        row = {"source": s, "state": "PLANNED", "fingerprint": "fp", "radii": ["0"]}
        process_image(io, row, journal, self.cfg, time.monotonic() + 60)
        self.assertEqual(row["state"], "COMPLETE")
        self.assertEqual(calls, [(0,0,0),(0,0,1),(1,0,0),(1,0,1)])
        for coord, plane in input_planes.items():
            np.testing.assert_array_equal(uploaded[coord], plane)
        process_image(io, row, journal, self.cfg, time.monotonic() + 60)
        self.assertEqual(calls[-1], "resume_verified")
        self.assertEqual(len(calls), 5)  # no new upload on resume


if __name__ == "__main__":
    unittest.main()
