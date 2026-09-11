# Generating H3lioviz Compatible Runs Without Lambda

The processor writes one zlib-compressed `pv-tim.XXXX.vts` structured grid per
timestep. Install the dependencies below (including `vtk`) in the processing
environment. The ParaView server uses its bundled VTK.

Fields are point data at the processed sample coordinates, with x/y/z in AU.
For cell-centered longitudes, values at 0° are interpolated between the last and
first longitude samples; that plane's coordinates and values are copied exactly
to 360°. Each file includes its simulation time as UTC epoch seconds. Coordinates
are stored in every timestep, so files may be larger than the former NetCDF output.
Downsampling must leave at least two radii, two latitudes, and three distinct
longitudes to form a volume.

Reprocess the original `tim.*.nc` files to generate this format. Evolution NetCDF
and JSON files, HelioWeb data, and solar images keep their existing formats.
Existing NetCDF runs still load, and VTS takes precedence if both outputs are in
the same run directory. Run IDs remain unchanged when reprocessing the same input.
The server does no seam blending. Legacy NetCDF runs retain cell-to-point
conversion, but must be reprocessed to VTS to receive the seam fix.

From the repository root, run the writer/processor regression checks with
`python -m unittest discover -s tests -p 'test_structured_grid.py'` in the processing
environment. To check the server with real runs, use
`pvpython tests/check_paraview_pipeline.py <vts-run-directory> --legacy-run <netcdf-run-directory>`.
The VTS fixture needs at least two timesteps with changing density. Add `--app`
inside the server environment to also check rendering and RPCs; this requires the
normal `/pvw/server/assets` directory. Use `--alternate-run <run-directory>` to
also check switching animation and satellites to a different date range.

| Instructions | Commands | Example |
|---|---|---|
|Enter the scripts/ directory|`cd scripts` |
|Create a Python3.12 environment to install dependencies (works with 3.9 as well)|`python3.12 -m venv venv`|
|Activate the Python environment|`source venv/bin/activate`|
|Install needed dependencies|`pip3 install -r requirements.txt`|
|Process the data by calling `process_output.py` with the path to the directory which contains all the data files. **If you are processing SWPC data** the final directory must match the following regex for run_id to be correctly identified as run_id is identified from the path. Otherwise a hash of the metadata will be used for the run_id. Regex for SWPC `^.*wsa_enlil_\d{5}\.\d*\.dbqs0`. **Note that the downsampling is configured by default** (8x radius, 2x latitude, and 2x longitude downsample) and you do not need to pass any flags. If you would like to create custom downsamples, you can run `python3 process_output.py -h` to find the appropriate flags.|`python3 process_output.py <path_to_nc_files>`|`python3 process_output.py ~/wsa_enlil_57671.77346046.dbqs01/`|
|Verify the processing was successful by looking for <path_to_nc_files>/pv-ready-data-<run_id>||`ls ~/wsa_enlil_57671.77346046.dbqs01/pv-ready-data-57671/`|
|Upload the run data to the appropriate s3 path. The example uses the aws CLI, but quicker copies of large number of files can be achieved with an open source tool called [s5cmd](https://github.com/peak/s5cmd) which parallelizes uploads.|`aws s3 sync <path_to_nc_files>/pv-ready-data-<run_id>/ s3://h3lioviz.<domain>/data/h3lioviz/pv-ready-data-<run_id>`|`aws s3 sync ~/wsa_enlil_57671.77346046.dbqs01/pv-ready-data-57671 s3://h3lioviz.bryandev.swx-trec.com/data/h3lioviz/pv-ready-data-57671`|
# Getting the New Run Populated into Dynamo DB
| Instructions | Commands | Example |
|---|---|---|
|Log into the paraview ec2|||
|Send a GET http request to the route `/h3lioviz/metadata/syncMetadata`. This will add any runs found in S3 that haven't had their metadata populated into the Dynamo DB table and will also remove any entries in Dynamo DB that do not have an associated metadata.json in S3.|`curl localhost/h3lioviz/metadata/syncMetadata`|
|You should get a status 'null' back. Anything else indicates an error.|
|(Optional) Validate that all the runs in S3 that have a metadata.json file have entries in the dynamodb table and vice versa|
|The run should now be available in the ParaView web application upon refreshing the page|
