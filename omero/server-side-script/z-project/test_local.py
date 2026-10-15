"""Local checks for Z_Projection.py; no OMERO server needed.

Run from this folder in the `omero` conda env:
    python -m unittest test_local -v
Image creation and upload are covered by the live check in README.md.
"""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np
from omero.rtypes import unwrap

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parent / "Z_Projection.py"

spec = importlib.util.spec_from_file_location("zproject_script", SCRIPT)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)
MAX, SUM, AVG = script.METHODS


class FakeImage:
    def __init__(self, image_id, name, size_z=5, pixel_type="uint16", pyramid=False):
        self.image_id, self.name, self.size_z = image_id, name, size_z
        self.pixel_type, self.pyramid = pixel_type, pyramid

    def getId(self):
        return self.image_id

    def getName(self):
        return self.name

    def getSizeZ(self):
        return self.size_z

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


class Naming(unittest.TestCase):
    def test_suffix_per_method(self):
        for method, expected in [(MAX, "image_001_mip.tif"), (SUM, "image_001_sum.tif"), (AVG, "image_001_avg.tif")]:
            self.assertEqual(script.output_name("image_001.ome.tif", method), expected)

    def test_extensions(self):
        for name, expected in [("sample.tiff", "sample_mip.tif"), ("sample.vsi", "sample_mip.tif"),
                               ("field.001.tif", "field.001_mip.tif"), ("noext", "noext_mip.tif")]:
            self.assertEqual(script.output_name(name, MAX), expected)

    def test_rejects_bad_input(self):
        self.assertRaises(ValueError, script.output_name, ".tif", MAX)
        self.assertRaises(ValueError, script.output_name, "a" * 250 + ".tif", MAX)


class PixelTypes(unittest.TestCase):
    def test_output_types(self):
        for source, method, expected in [("uint8", MAX, "uint8"), ("uint16", MAX, "uint16"), ("float", MAX, "float"),
                                         ("uint8", SUM, "uint32"), ("uint16", SUM, "uint32"), ("float", SUM, "float"),
                                         ("uint8", AVG, "float"), ("uint16", AVG, "float"), ("float", AVG, "float")]:
            self.assertEqual(script.output_pixel_type(source, method), expected)


class Project(unittest.TestCase):
    rng = np.random.default_rng(0)
    stacks = {"uint8": rng.integers(0, 255, (4, 64, 80)).astype(np.uint8),
              "uint16": rng.integers(0, 65535, (4, 64, 80)).astype(np.uint16),
              "float": rng.normal(100, 20, (4, 64, 80)).astype(np.float32)}

    def project(self, stack, method):
        source = "float" if stack.dtype.kind == "f" else stack.dtype.name
        return script.project(iter(stack), method, script.output_pixel_type(source, method))

    def test_max_keeps_type(self):
        for stack in self.stacks.values():
            result = self.project(stack, MAX)
            self.assertEqual(result.dtype, stack.dtype)
            self.assertTrue(np.array_equal(result, stack.max(axis=0)))

    def test_max_does_not_modify_input(self):
        stack = self.stacks["uint16"].copy()
        self.project(stack, MAX)
        self.assertTrue(np.array_equal(stack, self.stacks["uint16"]))

    def test_integer_sum_is_exact_uint32(self):
        for name in ("uint8", "uint16"):
            result = self.project(self.stacks[name], SUM)
            self.assertEqual(result.dtype, np.uint32)
            self.assertTrue(np.array_equal(result, self.stacks[name].sum(axis=0, dtype=np.uint64)))

    def test_large_uint16_sum_stays_exact(self):
        stack = np.full((300, 4, 4), 65535, dtype=np.uint16)
        self.assertEqual(int(self.project(stack, SUM)[0, 0]), 300 * 65535)  # beyond float32's exact range

    def test_uint32_overflow_rejected(self):
        stack = np.full((65538, 1, 1), 65535, dtype=np.uint16)
        self.assertRaises(ValueError, self.project, stack, SUM)

    def test_float_sum(self):
        result = self.project(self.stacks["float"], SUM)
        self.assertEqual(result.dtype, np.float32)
        expected = self.stacks["float"].astype(np.float64).sum(axis=0).astype(np.float32)
        self.assertTrue(np.array_equal(result, expected))

    def test_float_sum_overflow_rejected(self):
        stack = np.full((2, 1, 1), np.finfo(np.float32).max, dtype=np.float32)
        self.assertRaises(ValueError, self.project, stack, SUM)

    def test_average_is_float32_mean(self):
        for stack in self.stacks.values():
            result = self.project(stack, AVG)
            self.assertEqual(result.dtype, np.float32)
            self.assertTrue(np.array_equal(result, stack.astype(np.float64).mean(axis=0).astype(np.float32)))

    def test_non_finite_rejected(self):
        stack = self.stacks["float"].copy()
        stack[2, 0, 0] = np.nan
        for method in script.METHODS:
            self.assertRaises(ValueError, self.project, stack, method)

    def test_empty_rejected(self):
        self.assertRaises(ValueError, script.project, iter([]), MAX, "uint16")


class OutputPlanes(unittest.TestCase):
    def test_one_plane_per_channel_and_timepoint(self):
        stack = np.random.default_rng(1).integers(0, 4000, (4, 2, 3, 8, 8)).astype(np.uint16)  # z, c, t, y, x
        planes = list(script.output_planes(lambda z, c, t: stack[z, c, t], *stack.shape[:3], SUM, "uint32"))
        self.assertEqual([(z, c, t) for z, c, t, _ in planes], [(0, c, t) for c in range(2) for t in range(3)])
        for _, c, t, plane in planes:
            self.assertTrue(np.array_equal(plane, stack[:, c, t].sum(axis=0)))


class Dialog(unittest.TestCase):
    params = {p.name(): p for p in script.parameters()}

    def test_parameter_names(self):
        self.assertEqual(list(self.params), ["Data_Type", "IDs", "Name_Filter", "Projection",
                                             "Output_Dataset_ID", "New_Dataset_Name", "Dry_Run"])

    def test_projection_is_max_by_default(self):
        projection = self.params["Projection"]
        self.assertTrue(projection.useDefault)
        self.assertEqual(unwrap(projection.prototype), "Max intensity")
        self.assertEqual(unwrap(projection.values), list(script.METHODS))

    def test_dry_run_is_off_by_default(self):
        self.assertTrue(self.params["Dry_Run"].useDefault)
        self.assertIs(unwrap(self.params["Dry_Run"].prototype), False)


class Preflight(unittest.TestCase):
    def setUp(self):
        self.images = [FakeImage(2, "b.ome.tif"), FakeImage(1, "a.tif", pixel_type="uint8")]
        self.dataset = FakeDataset(9, "out")
        self.params = {"Data_Type": "Image", "IDs": [2, 1, 2], "Output_Dataset_ID": 9, "Dry_Run": True}

    def run_with(self, **changes):
        params = {k: v for k, v in {**self.params, **changes}.items() if v is not None}
        return script.run(FakeConn(*self.images, self.dataset), params)

    def test_dry_run_lists_plan(self):
        message, outputs = self.run_with()
        self.assertEqual(outputs, {})
        self.assertIn("2 image(s) -> dataset 9 'out'", message)
        self.assertIn("Projection: Max intensity.", message)
        self.assertLess(message.index("1 a.tif -> a_mip.tif (uint8)"),
                        message.index("2 b.ome.tif -> b_mip.tif (uint16)"))

    def test_dry_run_with_sum(self):
        message, _ = self.run_with(Projection=SUM, Output_Dataset_ID=None, New_Dataset_Name="sums")
        self.assertIn("-> new dataset 'sums'", message)
        self.assertIn("1 a.tif -> a_sum.tif (uint32)", message)

    def test_single_z_skipped_and_listed(self):
        self.images[1].size_z = 1
        self.images[1].pixel_type = "int32"  # not checked, because it is skipped
        message, _ = self.run_with()
        self.assertIn("1 image(s)", message)
        self.assertIn("Skipped (only one Z-plane):\n1 a.tif", message)

    def test_nothing_to_project(self):
        for image in self.images:
            image.size_z = 1
        message, outputs = self.run_with(Dry_Run=False, Output_Dataset_ID=None, New_Dataset_Name="never")
        self.assertEqual(outputs, {})
        self.assertIn("Nothing was written", message)

    def test_unsupported_and_large_images_rejected(self):
        self.images[0].pixel_type = "int32"
        self.images[1].pyramid = True
        message, _ = self.run_with()
        self.assertIn("Nothing was written", message)
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
