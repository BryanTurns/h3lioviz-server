"""Run with: python -m unittest discover -s tests -p 'test_structured_grid.py'."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import xarray as xr
from vtkmodules.util.numpy_support import vtk_to_numpy
from vtkmodules.vtkCommonDataModel import vtkPlane
from vtkmodules.vtkCommonExecutionModel import vtkStreamingDemandDrivenPipeline
from vtkmodules.vtkFiltersCore import vtkCutter
from vtkmodules.vtkIOXML import vtkXMLStructuredGridReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from process_output import process_directory
from structured_grid import write_vts


class StructuredGridTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def dataset(self, longitude=(30, 90, 150, 210, 270, 330)):
        radius = np.array([0.2, 0.7, 1.2])
        latitude = np.array([60, 20, -20, -60])
        longitude = np.array(longitude)
        # Deliberately different axis lengths and storage order expose transposes.
        values = (
            longitude[:, None, None]
            + latitude[None, :, None] * 10
            + radius[None, None, :] * 1000
        )
        return xr.Dataset(
            {"Density": (("time", "longitude", "latitude", "radius"), values[None])},
            coords={
                "radius": radius,
                "latitude": latitude,
                "longitude": longitude,
                "time": [np.datetime64("2024-05-10T12:34:56")],
            },
        )

    def read(self, ds):
        filename = self.path / "test.vts"
        write_vts(ds, filename)
        reader = vtkXMLStructuredGridReader()
        reader.SetFileName(str(filename))
        reader.Update()
        return reader, reader.GetOutput()

    def test_round_trip_values_coordinates_seam_and_time(self):
        ds = self.dataset()
        reader, grid = self.read(ds)
        self.assertEqual(grid.GetNumberOfCells(), 2 * 3 * 7)
        self.assertEqual(grid.GetCellData().GetNumberOfArrays(), 0)
        points = vtk_to_numpy(grid.GetPoints().GetData()).reshape(3, 4, 8, 3)
        values = vtk_to_numpy(grid.GetPointData().GetArray("Density")).reshape(3, 4, 8)
        np.testing.assert_array_equal(points[:, :, 0], points[:, :, -1])
        np.testing.assert_array_equal(values[:, :, 0], values[:, :, -1])
        expected = (
            ds.Density.isel(time=0).transpose("radius", "latitude", "longitude").values
        )
        np.testing.assert_array_equal(values[:, :, 1:-1], expected)
        np.testing.assert_array_equal(
            values[:, :, 0], (expected[:, :, 0] + expected[:, :, -1]) / 2
        )
        np.testing.assert_allclose(
            points[1, 1, 2],
            [0, 0.7 * np.cos(np.deg2rad(20)), 0.7 * np.sin(np.deg2rad(20))],
            atol=1e-15,
        )
        expected_time = float(
            (ds.time.values[0] - np.datetime64("1970-01-01")) / np.timedelta64(1, "s")
        )
        self.assertEqual(
            grid.GetFieldData().GetArray("TimeValue").GetValue(0), expected_time
        )
        self.assertEqual(
            reader.GetOutputInformation(0).Get(
                vtkStreamingDemandDrivenPipeline.TIME_STEPS()
            ),
            (expected_time,),
        )
        self.assertIn(
            b'compressor="vtkZLibDataCompressor"', (self.path / "test.vts").read_bytes()
        )

    def test_meridian_slice_covers_both_sides_at_and_near_seam(self):
        _, grid = self.read(self.dataset())
        for angle in (0, -1e-8, 1e-8, np.pi / 2):
            plane = vtkPlane()
            plane.SetNormal(np.sin(angle), np.cos(angle), 0)
            cutter = vtkCutter()
            cutter.SetInputData(grid)
            cutter.SetCutFunction(plane)
            cutter.Update()
            cut = cutter.GetOutput()
            self.assertGreater(cut.GetNumberOfCells(), 0)
            points = vtk_to_numpy(cut.GetPoints().GetData())
            along_plane = points @ np.array([np.cos(angle), -np.sin(angle), 0])
            self.assertLess(along_plane.min(), -0.9)
            self.assertGreater(along_plane.max(), 0.9)
            self.assertTrue(
                np.isfinite(vtk_to_numpy(cut.GetPointData().GetArray("Density"))).all()
            )

    def test_existing_endpoints_are_averaged_without_duplicate_cells(self):
        _, grid = self.read(self.dataset((0, 90, 180, 270, 360)))
        self.assertEqual(grid.GetNumberOfPoints(), 3 * 4 * 5)
        values = vtk_to_numpy(grid.GetPointData().GetArray("Density")).reshape(3, 4, 5)
        np.testing.assert_array_equal(values[:, :, 0], values[:, :, -1])
        self.assertEqual(values[0, 0, 0], 200 + 600 + 180)

    def test_nonuniform_longitude_uses_periodic_distance_weights(self):
        ds = self.dataset((10, 100, 200, 340))
        _, grid = self.read(ds)
        values = vtk_to_numpy(grid.GetPointData().GetArray("Density")).reshape(3, 4, 6)
        self.assertAlmostEqual(values[0, 0, 0], 800 + (340 / 3 + 10 * 2 / 3))

    def test_rejects_multiple_timesteps_and_over_downsampling(self):
        with self.assertRaisesRegex(ValueError, "exactly one timestep"):
            self.read(xr.concat([self.dataset(), self.dataset()], dim="time"))
        with self.assertRaisesRegex(ValueError, "radius"):
            self.read(self.dataset().isel(radius=slice(1)))

    def test_processor_writes_vts_metadata_and_preserves_evolution(self):
        shape = (1, 8, 4, 4)
        raw = xr.Dataset(
            {
                name: (("nblk", "n3", "n2", "n1"), np.full(shape, value))
                for name, value in {
                    "D": 1e-20,
                    "DP": 0.01,
                    "T": 1e5,
                    "B1": 1e-9,
                    "B2": 2e-9,
                    "B3": 3e-9,
                    "BP": 1.0,
                    "V1": 4e5,
                    "V2": 0.0,
                    "V3": 0.0,
                }.items()
            },
            attrs={
                "rundate_cal": "2024-05-10T12",
                "xalpha": 0.04,
                "project": "ENLIL.test",
            },
        )
        raw["X1"] = (
            ("nblk", "n1"),
            [[0.2 * 1.496e11, 0.4 * 1.496e11, 0.6 * 1.496e11, 0.8 * 1.496e11]],
        )
        raw["X2"] = (("nblk", "n2"), [np.deg2rad([30, 70, 110, 150])])
        raw["X3"] = (("nblk", "n3"), [np.deg2rad(np.arange(22.5, 360, 45))])
        for name in ("TIME", "DT", "NSTEP"):
            raw[name] = 0.0
        for i in range(2):
            raw["TIME"] = i * 3600.0
            raw.to_netcdf(self.path / f"tim.{i:04}.nc")
        evo = raw.isel(nblk=0, n1=0, n2=0, n3=0).expand_dims(nevo=2)
        evo["TIME"] = ("nevo", [0.0, 3600.0])
        evo.to_netcdf(self.path / "evo.earth.nc")
        process_directory(
            self.path,
            radius_downsample=2,
            latitude_downsample=2,
            longitude_downsample=2,
        )
        output = next(self.path.glob("pv-ready-data-*"))
        self.assertEqual(len(list(output.glob("pv-tim.*.vts"))), 2)
        self.assertEqual(list(output.glob("pv-tim.*.nc")), [])
        metadata = json.loads((output / "metadata.json").read_text())
        self.assertEqual(metadata["program"], "enlil")
        self.assertEqual(metadata["data_format"], "vts-point-data")
        self.assertTrue((output / "evo.earth.nc").exists())
        evolution = json.loads((output / "evo.earth.json").read_text())
        self.assertEqual(np.diff(evolution["coords"]["time"]["data"]), [3600])
        reader = vtkXMLStructuredGridReader()
        for i in range(2):
            reader.SetFileName(str(output / f"pv-tim.{i:04}.vts"))
            reader.Update()
            grid = reader.GetOutput()
            self.assertEqual(grid.GetNumberOfPoints(), 2 * 2 * 6)
            self.assertEqual(grid.GetCellData().GetNumberOfArrays(), 0)
            for name in (
                "Vr",
                "Density",
                "Pressure",
                "T",
                "Br",
                "Bx",
                "By",
                "Bz",
                "DP",
            ):
                self.assertIsNotNone(grid.GetPointData().GetArray(name))


if __name__ == "__main__":
    unittest.main()
