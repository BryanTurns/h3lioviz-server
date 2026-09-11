"""Run with the server's pvpython: benchmark_vts_playback.py RUN_DIRECTORY slices.

Uses timesteps 0, 1, 2, 80, 81, 82 for full runs, or the first six for a six-file
fixture. Runs two passes and reports medians from the second. The server's normal
/pvw/server/assets directory must be available. This includes all variable-range
queries and an explicit render after setting the animation time, but excludes
browser/network transport. Use mode "slices" to match the reported investigation.
"""

import argparse
import json
from pathlib import Path
import statistics
import sys
import time


def benchmark(run, mode):
    import paraview.simple as pvs

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pvw" / "server"))
    import models
    from app import App

    start = time.perf_counter()
    app = App(run.parent)
    app.model = models.Enlil(run)
    scene = pvs.GetAnimationScene()
    scene.UpdateAnimationUsingDataTimeSteps()
    scene.AnimationTime = scene.StartTime
    times = list(app.model._sources[0].TimestepValues)
    if len(times) < 6:
        raise ValueError("The playback benchmark requires at least six timesteps")
    app._init_filters()
    if mode == "all":
        for layer in (
            "lat_slice",
            "lon_slice",
            "radial_slice",
            "lat_streamlines",
            "lon_streamlines",
            "sat_fieldlines",
            "threshold",
        ):
            app.change_visibility(layer, "on")
    elif mode == "slices":
        for layer in (
            "lat_streamlines",
            "lon_streamlines",
            "sat_fieldlines",
            "cme",
            "cme_contours",
            "threshold",
            "radial_slice",
        ):
            app.change_visibility(layer, "off")
        for layer in ("lat_slice", "lon_slice"):
            app.change_visibility(layer, "on")
    elif mode == "no-fields":
        for layer in ("lat_streamlines", "lon_streamlines", "sat_fieldlines"):
            app.change_visibility(layer, "off")
    pvs.Render(app.view)
    print(
        json.dumps({"stage": "app_init", "seconds": time.perf_counter() - start}),
        flush=True,
    )
    indices = (0, 1, 2, 80, 81, 82) if len(times) > 82 else (0, 1, 2, 3, 4, 5)
    values = []
    for index in indices * 2:
        t = times[index]
        start = time.perf_counter()
        scene.AnimationTime = t
        set_time = time.perf_counter()
        for var in app.model._variable_mapping:
            app.get_variable_range(var)
        ranges = time.perf_counter()
        pvs.Render(app.view)
        render = time.perf_counter()
        values.append(
            [set_time - start, ranges - set_time, render - ranges, render - start]
        )
        print(
            json.dumps(
                {
                    "stage": "frame",
                    "index": index,
                    "set_time": values[-1][0],
                    "ranges": values[-1][1],
                    "render": values[-1][2],
                    "total": values[-1][3],
                }
            ),
            flush=True,
        )
    print(
        json.dumps(
            {
                "stage": "summary",
                "run": run.name,
                "median_seconds": [
                    statistics.median(row[col] for row in values[6:])
                    for col in range(4)
                ],
            }
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Processed run directory")
    parser.add_argument(
        "mode",
        nargs="?",
        default="slices",
        choices=("slices", "default", "all", "no-fields"),
        help="Visible layers; defaults to the two-slice view used in the report",
    )
    args = parser.parse_args()
    if not args.run.is_dir():
        parser.error(f"Run directory does not exist: {args.run}")
    benchmark(args.run.resolve(), args.mode)


if __name__ == "__main__":
    main()
