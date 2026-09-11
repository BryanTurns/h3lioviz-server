"""Integration check using pvpython and a processed run with at least two VTS files.

Usage: pvpython tests/check_paraview_pipeline.py VTS_RUN [--legacy-run NETCDF_RUN]
Add --app to exercise rendering/RPCs in the server environment.
"""

import argparse
import json
from pathlib import Path
import sys
import tempfile

import numpy as np
from paraview import servermanager
import paraview.simple as pvs
from vtkmodules.util.numpy_support import vtk_to_numpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pvw" / "server"))
import models


def check_detection():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        (directory / "unrelated.vts").touch()
        assert models.detect_program(directory) == "enlil"
        (directory / "data_0000.vts").touch()
        assert models.detect_program(directory) == "euhforia"
        (directory / "pv-tim.0000.vts").touch()
        assert models.detect_program(directory) == "enlil"
        (directory / "metadata.json").write_text(json.dumps({"program": "enlil"}))
        assert models.detect_program(directory, default="euhforia") == "enlil"


def check_model(new, legacy):
    assert models.detect_program(new) == "enlil"
    model = models.Enlil(new)
    assert len(model._sources) == 1, "VTS must feed point data directly from the reader"
    saved_data = model.data
    times = list(model._sources[0].TimestepValues)
    assert len(times) >= 2 and times[0] > 1e9 and times[1] > times[0], times
    densities = []
    for time in times[:2]:
        model.data.UpdatePipeline(time=time)
        grid = servermanager.Fetch(model.data)
        assert grid.GetCellData().GetNumberOfArrays() == 0
        assert grid.GetFieldData().GetArray("TimeValue").GetValue(0) == time
        extent = grid.GetExtent()
        shape = tuple(extent[i + 1] - extent[i] + 1 for i in (4, 2, 0))
        points = vtk_to_numpy(grid.GetPoints().GetData()).reshape(*shape, 3)
        np.testing.assert_array_equal(points[:, :, 0], points[:, :, -1])
        for key in model._variable_mapping:
            array = grid.GetPointData().GetArray(model.get_variable(key))
            assert array is not None, key
            values = vtk_to_numpy(array).reshape(shape)
            np.testing.assert_array_equal(values[:, :, 0], values[:, :, -1])
        densities.append(vtk_to_numpy(grid.GetPointData().GetArray("Density")).copy())
        outer_radius = np.linalg.norm(points, axis=-1).max()
        for angle in (0, -1e-8, 1e-8, np.pi / 2):
            cut = pvs.Slice(Input=model.data)
            cut.SliceType.Normal = [np.sin(angle), np.cos(angle), 0]
            cut.UpdatePipeline(time=time)
            sliced = servermanager.Fetch(cut)
            pts = vtk_to_numpy(sliced.GetPoints().GetData())
            along = pts @ np.array([np.cos(angle), -np.sin(angle), 0])
            assert along.min() < -outer_radius / 2
            assert along.max() > outer_radius / 2
            pvs.Delete(cut)
    assert not np.array_equal(*densities), (
        "Fixture needs changing density across timesteps"
    )
    cut = pvs.Slice(Input=model.data)
    cut.SliceType.Normal = [0, 0, 1]
    for directory in [legacy, new] if legacy else [new]:
        model.change_run(directory)
        expected_sources = 1 if any(directory.glob("pv-tim*.vts")) else 2
        assert len(model._sources) == expected_sources, "Unexpected runtime filter"
        assert model.data is saved_data
        cut.UpdatePipeline(time=times[0])
        fetched = servermanager.Fetch(cut)
        assert fetched.GetNumberOfCells() > 0
        assert fetched.GetPointData().GetArray("Density") is not None
    pvs.Delete(cut)
    pvs.Delete(model.data)
    for source in reversed(model._sources):
        pvs.Delete(source)
    print("PASS: model detection, timestamps, arrays, seam slices, and run switching")


def check_app(new, legacy, alternate):
    from app import App

    app = App(new.parent)
    app.load_model(new.name.removeprefix("pv-ready-data-"))
    scene = pvs.GetAnimationScene()
    scene.UpdateAnimationUsingDataTimeSteps()
    for time in list(app.model._sources[0].TimestepValues)[:2]:
        scene.AnimationTime = time
        for name in app.model._variable_mapping:
            assert np.isfinite(app.get_variable_range(name)).all(), name
        for surface in (
            app.lat_slice.slice,
            app.lon_slice.slice,
            app.radial_slice.slice,
        ):
            surface.UpdatePipeline(time=time)
            assert servermanager.Fetch(surface).GetNumberOfCells() > 0
        for contour in (app.cme, app.threshold):
            contour.UpdatePipeline(time=time)
        app.bvec.UpdatePipeline(time=time)
        assert app.bvec.PointData.GetArray("Bvec") is not None
        assert app._previous_time == app.get_current_time()
    for directory in [path for path in (legacy, alternate, new) if path is not None]:
        app._run_dir = directory.parent
        app.load_model(directory.name.removeprefix("pv-ready-data-"))
        times = list(app.model._sources[0].TimestepValues)
        assert scene.TimeKeeper.Time == times[0]
        assert scene.StartTime == times[0] and scene.EndTime == times[-1]
        assert app._previous_time == app.get_current_time()
        assert np.isfinite(app.get_variable_range("density")).all()
        expected_satellites = {
            name
            for name in app.model.satellites
            if "stereo" in name or name in ("mars", "venus", "mercury")
        }
        assert set(app.satellites) == expected_satellites
    print("PASS: App rendering, animation, ranges, slices, contours, and run switching")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vts_run", type=Path)
    parser.add_argument("--legacy-run", type=Path)
    parser.add_argument("--app", action="store_true")
    parser.add_argument(
        "--alternate-run", type=Path, help="App fixture with a different date range"
    )
    args = parser.parse_args()
    check_detection()
    check_model(args.vts_run, args.legacy_run)
    if args.app:
        check_app(args.vts_run, args.legacy_run, args.alternate_run)
