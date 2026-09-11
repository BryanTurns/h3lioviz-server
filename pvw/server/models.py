import json
from pathlib import Path

import numpy as np
import paraview.simple as pvs


def detect_program(dirname, default="enlil"):
    """Identify the model independently of the structured-grid extension."""
    metadata = dirname / "metadata.json"
    if metadata.exists():
        with metadata.open() as stream:
            program = json.load(stream).get("program")
        if program:
            return program.lower()
    if any(dirname.glob("pv-tim*.vts")) or any(dirname.glob("pv-*.nc")):
        return "enlil"
    if any(dirname.glob("data_*.vts")):
        return "euhforia"
    return default


class Model:
    """Heliosphere 3D model output"""

    def __init__(self, dirname: Path, variable_mapping: dict):
        """
        Parameters
        ----------
        dirname : Path
            Path of the directory containing the model output
        variable_mapping : dict
            Mapping of variable names from app to model. This is
            helpful for keeping the app with a consistent naming
            while letting the models handle the variables themselves.
        """
        self.dir = dirname
        required_variables = {
            "velocity",
            "density",
            "pressure",
            "temperature",
            "b",
            "bx",
            "by",
            "bz",
            "dp",
        }
        for x in required_variables:
            if x not in variable_mapping:
                raise ValueError(
                    f"The required variable {x} was not found in the model's variable mapping."
                )
        self._variable_mapping = variable_mapping
        # Start off with a default Earth satellite filling the dictionary
        # keeping track of the satellites
        self.satellites = {"earth": ModelSatellite("earth", (-1, 0, 0))}

    def get_variable(self, name: str) -> str:
        """Get the variable name associated with this model

        Parameters
        ----------
        name : str
            The name of the variable within the App

        Returns
        -------
        str
            The name of the variable within the model
        """
        return self._variable_mapping[name]


class Enlil(Model):
    """
    The 3D Enlil model simulation results
    """

    def __init__(self, dirname: Path):
        variable_mapping = {
            "velocity": "Vr",
            "density": "Density",
            "pressure": "Pressure",
            "temperature": "T",
            "b": "Br",
            "bx": "Bx",
            "by": "By",
            "bz": "Bz",
            "dp": "DP",
        }
        super().__init__(dirname=dirname, variable_mapping=variable_mapping)

        self._sources = self._create_sources()
        # Keep a stable output so switching between VTS and legacy NetCDF runs
        # does not disconnect the slices, contours, or satellite field lines.
        self.data = pvs.PassThrough(
            registrationName="enlil-pointdata", Input=self._sources[-1]
        )
        self._load_satellites()

    def _create_sources(self):
        filenames = self._get_filenames()
        if filenames[0].endswith(".vts"):
            reader = pvs.XMLStructuredGridReader(
                registrationName="enlil-data", FileName=filenames
            )
            reader.TimeArray = "TimeValue"
            return [reader]
        reader = pvs.NetCDFReader(registrationName="enlil-data", FileName=filenames)
        reader.Dimensions = "(longitude, latitude, radius)"
        point_data = pvs.CellDatatoPointData(
            registrationName="enlil-legacy-pointdata", Input=reader
        )
        point_data.ProcessAllArrays = 1
        return [reader, point_data]

    def _get_filenames(self):
        """
        Get VTS timesteps, legacy NetCDF timesteps, or one legacy NetCDF volume.

        Returns
        -------
        list(str)
            List of string filenames
        """
        # Prefer new point-data files when a directory also contains old output.
        vts_files = sorted(self.dir.glob("pv-tim*.vts"))
        if vts_files:
            return [str(x) for x in vts_files]
        legacy_filename = self.dir / "pv-data-3d.nc"
        if legacy_filename.exists():
            # Old-style processing with a single giant file
            return [str(legacy_filename)]
        # New processing with a single file for each timestep
        filenames = [str(x) for x in sorted(self.dir.glob("pv-tim*.nc"))]
        if not filenames:
            raise ValueError(f"No ENLIL timestep files found in {self.dir}")
        return filenames

    def _load_satellites(self):
        self.satellites = {"earth": ModelSatellite("earth", (-1, 0, 0))}
        sat_files = self.dir.glob("evo.*.json")
        for sat_file in sat_files:
            # strip the extra components from the name
            # Example: evo.sat_name.json
            name = sat_file.name[4:-5]
            self.satellites[name] = EnlilSatellite(name, sat_file)

    def change_run(self, dirname):
        """
        Change to a different model run.

        Parameters
        ----------
        dirname : Path
            Path of the directory containing the ENLIL model output
        """
        self.dir = dirname
        sources = self._create_sources()
        self.data.Input = sources[-1]
        for source in reversed(self._sources):
            pvs.Delete(source)
        self._sources = sources
        # Reload the satellite files for this run
        self._load_satellites()


class Euhforia(Model):
    """
    The 3D EUHFORIA model simulation results
    """

    def __init__(self, dirname: Path):
        """
        Parameters
        ----------
        dirname : Path
            Path of the directory containing the EUHFORIA model output
        """
        variable_mapping = {
            "velocity": "vr",
            "density": "n-scaled",
            "pressure": "P",
            "temperature": "T",
            "b": "Br",
            "bx": "Bx",
            "by": "By",
            "bz": "Bz",
            "dp": "DP",
        }
        super().__init__(dirname=dirname, variable_mapping=variable_mapping)
        # Glob to list all files in the data directory
        fnames = [str(fname) for fname in self.dir.glob("data_*.vts")]
        # create a new 'XML Structured Grid Reader'
        self._input_data = pvs.XMLStructuredGridReader(
            registrationName="euhforia-data", FileName=fnames
        )
        self._input_data.CellArrayStatus = ["vr", "n", "P", "Br", "Bx", "By", "Bz"]
        self._input_data.TimeArray = "None"

        # Now scale all the arrays we need to work with
        self.data = pvs.CellDatatoPointData(
            registrationName="euhforia-pointdata", Input=self._input_data
        )
        self.data.ProcessAllArrays = 1
        self.data.PassCellData = 1

        # now calculate the density with rho * r**2
        self.data = pvs.Calculator(
            registrationName="euhforia-calculator", Input=self.data
        )
        self.data.AttributeType = "Point Data"
        self.data.ResultArrayName = "n-scaled"
        self.data.Function = "n * (coordsX^2 + coordsY^2 + coordsZ^2)"

        # Earth is at +1 X in Euhforia
        self.satellites["earth"].position = (1, 0, 0)

    def change_run(self, dirname):
        """
        Change to a different model run.

        Parameters
        ----------
        dirname : Path
            Path of the directory containing the EUHFORIA model output
        """
        self.dir = dirname
        fnames = [str(fname) for fname in self.dir.glob("data_*.vts")]
        self._input_data.FileName = fnames


class ModelSatellite:
    """
    A satellite or observation point in space.

    Parameters
    ----------
    name : str
        Name of the satellite
    """

    def __init__(self, name, position=(0, 0, 0)):
        self.name = name
        self.position = position

    def get_position(self, time):
        """The position of the satellite closest to the given time

        time : datetime-like
            Time of interest

        Returns
        -------
        The closest (X, Y, Z) position of the satellite to the requested time.
        """
        # Default just return the current position
        return self.position


class EnlilSatellite(ModelSatellite):
    def __init__(self, name, fpath):
        """
        Enlil time-series JSON of satellite data.

        This class will read in the given JSON file that contains
        the Enlil output for a specific satellite.

        Parameters
        ----------
        name : str
            Name of the satellite
        fpath : Path
            Path to the "evo" data file
        """
        super().__init__(name=name)
        with open(fpath) as f:
            self.json = json.loads(f.read())
            # all times are based off of unix epoch
            self.times = np.datetime64("1970-01-01") + np.array(
                self.json["coords"]["time"]["data"]
            ).astype("timedelta64[s]")

            # iterate over all data variables and set those as
            # attributes on the object
            for var in ["X", "Y", "Z"]:
                setattr(self, var, np.array(self.json["data_vars"][var]["data"]))

    def get_position(self, time):
        """
        Get the position at the given time.

        time : datetime-like
            Time of interest

        Returns
        -------
        Tuple (X, Y, Z) of the position of the satellite to the requested time.
        """
        loc = np.argmin(np.abs(np.datetime64(time) - self.times))
        return self.X[loc], self.Y[loc], self.Z[loc]
