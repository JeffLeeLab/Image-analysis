"""Local checks for Rolling_Ball_Background.py; no OMERO server needed.

Run from this folder in the `omero` conda env (needs SciPy):
    python -m unittest test_local -v
Image creation and upload are covered by the live check in README.md.
"""
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from omero.rtypes import unwrap

sys.dont_write_bytecode = True  # keep __pycache__ out of the pinned algorithm's folder
HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "Rolling_Ball_Background.py"
PINNED = HERE.parents[2] / "people/AlfredoCastello/Zhiling-Marko-HIV/src/imagej_rolling_ball.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = load(SCRIPT, "bgsub_script")
pinned = load(PINNED, "pinned_rolling_ball")


class FakeImage:
    def __init__(self, image_id, name, channels, pixel_type="uint16", pyramid=False):
        self.image_id, self.name, self.channels = image_id, name, channels
        self.pixel_type, self.pyramid = pixel_type, pyramid

    def getId(self):
        return self.image_id

    def getName(self):
        return self.name

    def getChannels(self, noRE=False):
        return [SimpleNamespace(getLogicalChannel=lambda n=n: SimpleNamespace(name=n)) for n in self.channels]

    def getPrimaryPixels(self):
        store = SimpleNamespace(requiresPixelsPyramid=lambda: self.pyramid, close=lambda: None)
        return SimpleNamespace(getPixelsType=lambda: SimpleNamespace(getValue=lambda: self.pixel_type),
                               _prepareRawPixelsStore=lambda: store)


class FakeDataset:
    def __init__(self, dataset_id, name, children=(), can_link=True):
        self.dataset_id, self.name, self.children, self.can_link = dataset_id, name, list(children), can_link

    def getId(self):
        return self.dataset_id

    def getName(self):
        return self.name

    def canLink(self):
        return self.can_link

    def listChildren(self):
        return iter(self.children)


class FakeConn:
    def __init__(self, *objects):
        self.objects = {(type(o).__name__.replace("Fake", ""), o.getId()): o for o in objects}

    def getObject(self, kind, object_id):
        return self.objects.get((kind, object_id))


class AlgorithmIsPinned(unittest.TestCase):
    def test_pinned_file_hash(self):
        self.assertEqual(hashlib.sha256(PINNED.read_bytes()).hexdigest(), script.ALGORITHM_SHA256)

    def test_block_is_verbatim(self):
        text = SCRIPT.read_text()
        block = text.split("# --- BEGIN pinned imagej_rolling_ball.py ---\n")[1]
        block = block.split("# --- END pinned imagej_rolling_ball.py ---\n")[0]
        self.assertEqual(block, "".join(PINNED.read_text().splitlines(keepends=True)[20:]))


class Naming(unittest.TestCase):
    def test_spec_examples(self):
        for name, radii, expected in [("image_001.ome.tif", [0, 50, 100], "image_001_bgs-0-50-100.tif"),
                                      ("sample.tiff", [12.5, 0, 100], "sample_bgs-12.5-0-100.tif"),
                                      ("sample.vsi", [0, 0, 0], "sample_bgs-0-0-0.tif"),
                                      ("field.001.tif", [0, 50, 100], "field.001_bgs-0-50-100.tif"),
                                      ("noext", [5], "noext_bgs-5.tif")]:
            self.assertEqual(script.output_name(name, radii), expected)

    def test_only_known_extensions_removed(self):
        lif = "20261012_SINV_MOI5_24hpi_cellmask_IFI27_IFNb_MX1_DAPI_63X.lif_-_P_10"
        for name, expected in [(lif + ".tif", lif + "_bgs-5.tif"), (lif, lif + "_bgs-5.tif"),
                               ("Experiment.lif [Position 10]", "Experiment.lif [Position 10]_bgs-5.tif"),
                               ("cells_0.5uM", "cells_0.5uM_bgs-5.tif"), ("cells_0.5uM.czi", "cells_0.5uM_bgs-5.tif"),
                               ("IMG.TIF", "IMG_bgs-5.tif"), ("sample.OME.TIFF", "sample_bgs-5.tif"),
                               ("stain.MX1", "stain.MX1_bgs-5.tif")]:
            self.assertEqual(script.output_name(name, [5]), expected)

    def test_series_names_stay_distinct(self):
        names = ([f"Experiment.lif_-_P_{n}" for n in range(10, 19)]
                 + [f"Experiment.lif [Position {n}]" for n in range(10, 19)])
        self.assertEqual(len({script.output_name(name, [5]) for name in names}), len(names))

    def test_projection_marks_step_order(self):
        for projection, expected in [(script.PROJECTION_OFF, "image_001_bgs-0-50.tif"),
                                     (script.MIP_FIRST, "image_001_mip_bgs-0-50.tif"),
                                     (script.MIP_LAST, "image_001_bgs-0-50_mip.tif")]:
            self.assertEqual(script.output_name("image_001.ome.tif", [0, 50], projection), expected)

    def test_radius_tokens(self):
        for value, expected in [(50.0, "50"), (12.50, "12.5"), (1e2, "100"), (-0.0, "0"), (0.25, "0.25")]:
            self.assertEqual(script.radius_token(value), expected)

    def test_rejects_bad_input(self):
        for call in (lambda: script.output_name(".tif", [1]), lambda: script.radius_token(float("nan")),
                     lambda: script.radius_token(-1), lambda: script.output_name("a" * 250 + ".tif", [1, 2])):
            self.assertRaises(ValueError, call)


class CorrectPlane(unittest.TestCase):
    rng = np.random.default_rng(0)
    planes = {"uint8": rng.integers(0, 255, (64, 80)).astype(np.uint8),
              "uint16": rng.integers(0, 4000, (64, 80)).astype(np.uint16),
              "float32": rng.normal(100, 20, (64, 80)).astype(np.float32)}

    def test_zero_radius_is_exact_copy(self):
        for plane in self.planes.values():
            result = script.correct_plane(plane, 0)
            self.assertEqual(result.dtype, plane.dtype)
            self.assertTrue(np.array_equal(result, plane))
            self.assertIsNot(result, plane)

    def test_positive_radius_matches_pinned_module(self):
        for plane in self.planes.values():
            corrected, _ = pinned.subtract_background_image(plane, pinned.rolling_ball_background(plane, 12.5))
            if plane.dtype.kind == "u":
                corrected = np.floor(np.clip(corrected, 0, np.iinfo(plane.dtype).max) + 0.5)
            result = script.correct_plane(plane, 12.5)
            self.assertEqual(result.dtype, plane.dtype)
            self.assertTrue(np.array_equal(result, corrected.astype(plane.dtype)))

    def test_rounds_half_up_and_clips(self):
        plane = np.array([[3, 4, 0, 255]], dtype=np.uint8)
        background = np.array([[0.5, 0.5, 0.5, -1.0]])
        with patch.object(script, "rolling_ball_background", return_value=background):
            self.assertEqual(script.correct_plane(plane, 1).tolist(), [[3, 4, 0, 255]])

    def test_non_finite_rejected_even_when_copying(self):
        plane = self.planes["float32"].copy()
        plane[0, 0] = np.nan
        for radius in (0, 5):
            self.assertRaises(ValueError, script.correct_plane, plane, radius)


class ZProjection(unittest.TestCase):
    rng = np.random.default_rng(1)
    stack = rng.integers(0, 4000, (4, 2, 1, 64, 80)).astype(np.uint16)  # z, c, t, y, x
    radii = [0, 12.5]

    def planes(self, projection, stack=None):
        stack = self.stack if stack is None else stack
        get_plane = lambda z, c, t: stack[z, c, t]
        return list(script.output_planes(get_plane, *stack.shape[:3], self.radii, projection))

    def test_off_keeps_every_plane(self):
        planes = self.planes(script.PROJECTION_OFF)
        self.assertEqual(len(planes), 8)
        for z, c, t, plane in planes:
            self.assertTrue(np.array_equal(plane, script.correct_plane(self.stack[z, c, t], self.radii[c])))

    def test_project_then_subtract(self):
        planes = self.planes(script.MIP_FIRST)
        self.assertEqual([(z, c, t) for z, c, t, _ in planes], [(0, 0, 0), (0, 1, 0)])
        for _, c, t, plane in planes:
            expected = script.correct_plane(self.stack[:, c, t].max(axis=0), self.radii[c])
            self.assertTrue(np.array_equal(plane, expected))

    def test_subtract_then_project(self):
        planes = self.planes(script.MIP_LAST)
        self.assertEqual([(z, c, t) for z, c, t, _ in planes], [(0, 0, 0), (0, 1, 0)])
        for _, c, t, plane in planes:
            expected = np.max([script.correct_plane(p, self.radii[c]) for p in self.stack[:, c, t]], axis=0)
            self.assertTrue(np.array_equal(plane, expected))

    def test_zero_radius_is_raw_mip_in_both_orders(self):
        for projection in (script.MIP_FIRST, script.MIP_LAST):
            _, _, _, plane = self.planes(projection)[0]
            self.assertEqual(plane.dtype, self.stack.dtype)
            self.assertTrue(np.array_equal(plane, self.stack[:, 0, 0].max(axis=0)))

    def test_orders_differ(self):
        first, last = self.planes(script.MIP_FIRST)[1][3], self.planes(script.MIP_LAST)[1][3]
        self.assertFalse(np.array_equal(first, last))

    def test_nan_slice_rejected_in_both_orders(self):
        stack = self.stack.astype(np.float32)
        stack[2, 0, 0, 0, 0] = np.nan
        for projection in (script.MIP_FIRST, script.MIP_LAST):
            self.assertRaises(ValueError, self.planes, projection, stack)


class Dialog(unittest.TestCase):
    params = {p.name(): p for p in script.parameters()}

    def test_parameter_names(self):
        expected = (["Data_Type", "IDs", "Name_Filter"] + [f"Channel_{i}_Radius" for i in range(1, 9)]
                    + ["Z_Projection", "Output_Dataset_ID", "New_Dataset_Name", "Dry_Run"])
        self.assertEqual(list(self.params), expected)

    def test_radius_is_optional_without_default(self):
        for i in range(1, 9):
            radius = self.params[f"Channel_{i}_Radius"]
            self.assertTrue(radius.optional)
            self.assertFalse(radius.useDefault)
            self.assertEqual(unwrap(radius.min), 0)

    def test_projection_is_off_by_default(self):
        projection = self.params["Z_Projection"]
        self.assertTrue(projection.useDefault)
        self.assertEqual(unwrap(projection.prototype), "Off")
        self.assertEqual(unwrap(projection.values), list(script.PROJECTIONS))

    def test_dry_run_is_off_by_default(self):
        self.assertTrue(self.params["Dry_Run"].useDefault)
        self.assertIs(unwrap(self.params["Dry_Run"].prototype), False)


class Preflight(unittest.TestCase):
    def setUp(self):
        self.images = [FakeImage(2, "b.ome.tif", ["DAPI", "GFP"]), FakeImage(1, "a.tif", ["DAPI", "GFP"])]
        self.dataset = FakeDataset(9, "out")
        self.params = {"Data_Type": "Image", "IDs": [2, 1, 2], "Channel_1_Radius": 0.0,
                       "Channel_2_Radius": 50.0, "Output_Dataset_ID": 9, "Dry_Run": True}

    def run_with(self, **changes):
        params = {k: v for k, v in {**self.params, **changes}.items() if v is not None}
        return script.run(FakeConn(*self.images, self.dataset), params)

    def test_dry_run_lists_plan(self):
        message, outputs = self.run_with()
        self.assertEqual(outputs, {})
        self.assertIn("2 image(s) -> dataset 9 'out'", message)
        self.assertIn("1 DAPI=0 (copied), 2 GFP=50", message)
        self.assertLess(message.index("1 a.tif -> a_bgs-0-50.tif"), message.index("2 b.ome.tif -> b_bgs-0-50.tif"))

    def test_dry_run_with_projection(self):
        message, _ = self.run_with(Z_Projection=script.MIP_FIRST)
        self.assertIn("Z projection: Max projection, then subtract.", message)
        self.assertIn("1 a.tif -> a_mip_bgs-0-50.tif", message)

    def test_missing_radius_is_not_zero(self):
        message, _ = self.run_with(Channel_2_Radius=None)
        self.assertIn("Nothing was written", message)
        self.assertIn("radius for channel(s) 2", message)

    def test_radius_for_absent_channel_rejected(self):
        message, _ = self.run_with(Channel_3_Radius=10.0)
        self.assertIn("clear the radius for channel(s) 3", message)

    def test_channel_layout_must_match(self):
        self.images[0].channels = ["GFP", "DAPI"]
        message, _ = self.run_with()
        self.assertIn("differ from", message)

    def test_unsupported_and_large_images_rejected(self):
        self.images[0].pixel_type = "int32"
        self.images[1].pyramid = True
        message, _ = self.run_with()
        self.assertIn("pixel type int32", message)
        self.assertIn("too large", message)

    def test_exactly_one_destination(self):
        for changes in ({"New_Dataset_Name": "x"}, {"Output_Dataset_ID": None}):
            message, _ = self.run_with(**changes)
            self.assertIn("exactly one of Output_Dataset_ID or New_Dataset_Name", message)

    def test_dataset_input(self):
        self.dataset.children = self.images
        message, _ = self.run_with(Data_Type="Dataset", IDs=[9], Name_Filter="a*")
        self.assertIn("1 image(s)", message)
        message, _ = self.run_with(Data_Type="Dataset", IDs=[9, 9])
        self.assertIn("exactly one dataset ID", message)


if __name__ == "__main__":
    unittest.main()
