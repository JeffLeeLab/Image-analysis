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

    def test_z_range_in_name(self):
        self.assertEqual(script.output_name("image_001.ome.tif", MAX, (3, 15)), "image_001_mip3-15.tif")
        self.assertEqual(script.output_name("sample.tiff", SUM, (7, 7)), "sample_sum7-7.tif")

    def test_rejects_bad_input(self):
        self.assertRaises(ValueError, script.output_name, ".tif", MAX)
        self.assertRaises(ValueError, script.output_name, "a" * 250 + ".tif", MAX)
        self.assertRaises(ValueError, script.output_name, "a" * 245 + ".tif", MAX, (100, 200))


class ZRange(unittest.TestCase):
    def test_requested(self):
        self.assertEqual(script.requested_z({}), (None, None))
        self.assertEqual(script.requested_z({"Z_Start": 3, "Z_End": 15}), (3, 15))
        self.assertEqual(script.requested_z({"Z_Start": 7, "Z_End": 7}), (7, 7))

    def test_requested_rejects_bad_input(self):
        for params in ({"Z_Start": 15, "Z_End": 3}, {"Z_Start": 0}, {"Z_End": 0}):
            self.assertRaises(ValueError, script.requested_z, params)

    def test_per_image_range(self):
        for requested, expected in [((None, None), None), ((3, 15), (3, 15)), ((5, None), (5, 30)),
                                    ((None, 4), (1, 4)), ((30, None), (30, 30)), ((1, 30), (1, 30))]:
            self.assertEqual(script.image_z_range(requested, 30), expected)

    def test_range_beyond_stack_rejected(self):
        for requested in ((3, 31), (31, None), (None, 31), (31, 40)):
            self.assertRaises(ValueError, script.image_z_range, requested, 30)

    def test_indices_are_zero_based(self):
        self.assertEqual(script.z_indices(None, 5), range(5))
        self.assertEqual(script.z_indices((3, 15), 30), range(2, 15))

    def test_text(self):
        for requested, expected in [((None, None), "all"), ((3, 15), "3-15"), ((5, None), "5-last"),
                                    ((None, 4), "1-4")]:
            self.assertEqual(script.z_text(requested), expected)


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
        planes = list(script.output_planes(lambda z, c, t: stack[z, c, t], range(4), 2, 3, SUM, "uint32"))
        self.assertEqual([(z, c, t) for z, c, t, _ in planes], [(0, c, t) for c in range(2) for t in range(3)])
        for _, c, t, plane in planes:
            self.assertTrue(np.array_equal(plane, stack[:, c, t].sum(axis=0)))

    def test_only_chosen_planes(self):
        stack = np.random.default_rng(2).integers(0, 4000, (6, 1, 1, 8, 8)).astype(np.uint16)
        for method, out_type, expected in [(MAX, "uint16", stack[1:4, 0, 0].max(axis=0)),
                                           (SUM, "uint32", stack[1:4, 0, 0].sum(axis=0)),
                                           (AVG, "float", stack[1:4, 0, 0].mean(axis=0, dtype=np.float64))]:
            (_, _, _, plane), = script.output_planes(lambda z, c, t: stack[z, c, t], script.z_indices((2, 4), 6),
                                                     1, 1, method, out_type)
            self.assertTrue(np.array_equal(plane, expected.astype(plane.dtype)))


class Dialog(unittest.TestCase):
    params = {p.name(): p for p in script.parameters()}

    def test_parameter_names(self):
        self.assertEqual(list(self.params), ["Data_Type", "IDs", "Name_Filter", "Projection", "Z_Start", "Z_End",
                                             "Output_Dataset_ID", "New_Dataset_Name", "Dry_Run"])

    def test_z_range_is_blank_by_default(self):
        for name in ("Z_Start", "Z_End"):
            param = self.params[name]
            self.assertTrue(param.optional)
            self.assertFalse(param.useDefault)
            self.assertEqual(unwrap(param.min), 1)

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
        self.assertIn("Z-planes: all.", message)
        self.assertLess(message.index("1 a.tif -> a_mip.tif (uint8)"),
                        message.index("2 b.ome.tif -> b_mip.tif (uint16)"))

    def test_dry_run_with_z_range(self):
        message, _ = self.run_with(Z_Start=2, Z_End=4)
        self.assertIn("Z-planes: 2-4.", message)
        self.assertIn("1 a.tif -> a_mip2-4.tif (uint8)", message)
        self.assertIn("2 b.ome.tif -> b_mip2-4.tif (uint16)", message)

    def test_open_ended_range_per_image(self):
        self.images[0].size_z = 8
        message, _ = self.run_with(Z_Start=3)
        self.assertIn("Z-planes: 3-last.", message)
        self.assertIn("1 a.tif -> a_mip3-5.tif", message)
        self.assertIn("2 b.ome.tif -> b_mip3-8.tif", message)

    def test_range_beyond_any_image_stops_batch(self):
        self.images += [FakeImage(3, "c.tif", size_z=3), FakeImage(4, "d.tif", size_z=2)]
        message, outputs = self.run_with(IDs=[1, 2, 3, 4], Z_Start=2, Z_End=4, Dry_Run=False)
        self.assertEqual(outputs, {})
        self.assertIn("Nothing was written", message)
        self.assertIn("Image 3 c.tif: Z-planes 2-4 do not fit its 3 Z-planes", message)
        self.assertIn("Image 4 d.tif: Z-planes 2-4 do not fit its 2 Z-planes", message)

    def test_single_z_skipped_not_rejected_with_z_range(self):
        self.images[1].size_z = 1
        message, _ = self.run_with(Z_Start=2, Z_End=4)
        self.assertIn("1 image(s)", message)
        self.assertIn("Skipped (only one Z-plane):\n1 a.tif", message)

    def test_start_after_end_rejected(self):
        message, outputs = self.run_with(Z_Start=4, Z_End=2, Dry_Run=False)
        self.assertEqual(outputs, {})
        self.assertIn("Nothing was written", message)
        self.assertIn("Z_Start (4) is after Z_End (2)", message)

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
