"""Write processed ENLIL samples as a periodic, point-data structured grid."""

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkDataObject, vtkStructuredGrid
from vtkmodules.vtkIOXML import vtkXMLStructuredGridWriter


def write_vts(ds, filename):
    """Write one processed TIM dataset, with coordinates in AU and UTC epoch time.

    Samples remain at their original radius/latitude/longitude coordinates.
    ENLIL longitudes are usually cell centers: interpolate across the periodic
    boundary to add a 0-degree plane, then duplicate it exactly at 360 degrees.
    This closes both the geometry and every point-data array before slicing.
    """
    if ds.sizes.get("time") != 1:
        raise ValueError("A VTS file must contain exactly one timestep")
    epoch_seconds = float(
        (ds.time.values[0] - np.datetime64("1970-01-01T00:00:00"))
        / np.timedelta64(1, "s")
    )
    ds = ds.isel(time=0, drop=True)
    axes = ("radius", "latitude", "longitude")
    for axis in axes:
        values = ds[axis].values
        if values.ndim != 1 or len(values) < 2 or not np.isfinite(values).all():
            raise ValueError(f"{axis} must contain at least two finite coordinates")

    # Sort the samples after wrapping negative longitudes, and combine any
    # existing 0/360 endpoints into a single point-data plane.
    longitude = np.mod(ds.longitude.values.astype(np.float64), 360.0)
    longitude[np.isclose(longitude, 360, rtol=0, atol=1e-5)] = 0
    longitude[np.isclose(longitude, 0, rtol=0, atol=1e-5)] = 0
    ds = ds.assign_coords(longitude=longitude).sortby("longitude")
    if len(np.unique(longitude)) != len(longitude):
        ds = ds.groupby("longitude").mean()
    longitude = ds.longitude.values
    if len(longitude) < 3:
        raise ValueError(
            "At least three distinct longitudes are needed to wrap the grid"
        )

    has_zero = longitude[0] == 0
    if has_zero:
        output_longitude = np.append(longitude, 360.0)
    else:
        output_longitude = np.concatenate(([0.0], longitude, [360.0]))
    radius, latitude, phi = np.meshgrid(
        ds.radius.values.astype(np.float64),
        np.deg2rad(ds.latitude.values.astype(np.float64)),
        np.deg2rad(output_longitude),
        indexing="ij",
    )
    sin_phi, cos_phi = np.sin(phi), np.cos(phi)
    sin_lat, cos_lat = np.sin(latitude), np.cos(latitude)
    # Keep planes on the Cartesian axes exact, including the opposite meridian.
    for component in (sin_phi, cos_phi, sin_lat, cos_lat):
        component[np.abs(component) < 1e-15] = 0.0
    xyz = np.stack(
        (radius * cos_lat * cos_phi, radius * cos_lat * sin_phi, radius * sin_lat),
        axis=-1,
    )
    xyz[:, :, -1] = xyz[:, :, 0]
    points = vtkPoints()
    points.SetData(numpy_to_vtk(xyz.reshape(-1, 3), deep=True))
    grid = vtkStructuredGrid()
    # VTK's first dimension varies fastest, as longitude does in these arrays.
    grid.SetDimensions(*xyz.shape[:3][::-1])
    grid.SetPoints(points)
    for name, variable in ds.data_vars.items():
        if not set(axes).issubset(variable.dims):
            continue
        values = variable.transpose(*axes).values
        if has_zero:
            values = np.concatenate((values, values[:, :, :1]), axis=2)
        else:
            # Linear interpolation between the samples on either side of 0.
            weight = (360.0 - longitude[-1]) / (longitude[0] + 360.0 - longitude[-1])
            seam = (1.0 - weight) * values[:, :, -1:] + weight * values[:, :, :1]
            values = np.concatenate((seam, values, seam), axis=2)
        array = numpy_to_vtk(np.ascontiguousarray(values).reshape(-1), deep=True)
        array.SetName(name)
        grid.GetPointData().AddArray(array)

    # XML readers expose TimeValue as the real timestep of each file in a series.
    grid.GetInformation().Set(vtkDataObject.DATA_TIME_STEP(), epoch_seconds)
    writer = vtkXMLStructuredGridWriter()
    writer.SetFileName(str(filename))
    writer.SetInputData(grid)
    writer.SetDataModeToAppended()
    writer.SetCompressorTypeToZLib()
    if writer.Write() != 1:
        raise OSError(f"Could not write structured grid: {filename}")
