# VTS playback investigation — 2026-09-11

The playback regression is reproducible with a two-slice view: the ecliptic
and meridional slices visible, with CME surfaces, thresholds, radial slices, and
all field lines hidden. The initial Base64/zlib VTS encoding introduced avoidable decoding
and decompression work on every timestep. Keeping the structured grid and seam
closure, a prototype using raw appended LZ4 data and the original field dtypes
beat both the initial VTS files and legacy NetCDF in this test.

| Data format | Median complete timestep | Median reader-only update | Mean file size, six sampled timesteps |
| --- | ---: | ---: | ---: |
| Legacy NetCDF | 386 ms | 26–27 ms | 7.28 MiB |
| Initial Base64/zlib VTS | 503 ms; repeat 547 ms | 200–210 ms | 12.17 MiB |
| Optimized VTS: original field dtypes, raw appended LZ4 | 291 ms | 5.9 ms | 9.75 MiB |

The initial VTS view took 30–42% longer per timestep than NetCDF. The prototype
reduced complete timestep latency by 42–47% versus the initial VTS files and by 25% versus
NetCDF. Reader-only improvements are much larger than whole-frame improvements
because slicing, rendering, and other pipeline work still take time.

These are local measurements using the existing ParaView 5.10.1 OSMesa Docker
image on an eight-logical-CPU host. They exclude browser rendering and WebSocket
transport. They are comparative benchmarks, not predicted production frame rates.

The two runs were `data/pv-ready-data-0d4d937e` (NetCDF) and
`data/pv-ready-data-12345` (VTS), each containing 169 timesteps. Original sample
values matched exactly for all nine physical fields in the first timestep.
The VTS run is 2,077 MiB versus 1,230 MiB for NetCDF, approximately 69% larger.
VTS has fewer cells (166,257 versus 172,800), so this is not a higher-resolution
simulation. Both paths produce structured grids with double-precision coordinates.

Two avoidable costs were introduced by the new writer in
[`scripts/structured_grid.py`](../scripts/structured_grid.py):

1. `SetDataModeToAppended()` still leaves Base64 encoding enabled. Every timestep
   is read, decoded, and zlib-decompressed. Disabling Base64 alone cut measured
   read time from about 200 ms to 111 ms. Using raw appended LZ4 with all current
   Float64 values unchanged reduced it further to 15 ms. Those two changes are
   lossless and do not alter the grid, fields, or seam.
2. The longitude interpolation weight is a NumPy Float64 scalar. With the
   processing environment used for these files, the seam calculation promotes
   seven Float32 arrays to Float64, and concatenating the seam with the original
   samples promotes each entire field. `T`, `DP`, `Bx`, `By`, `Bz`, `Br`, and `Vr`
   were Float32 in NetCDF; all nine fields are Float64 in VTS. Retaining the
   original dtypes reduced the VTK dataset memory estimate from 16,561 KiB to
   11,731 KiB. Density and pressure remain Float64, as do the coordinates.

The writer now uses the following settings:

```python
# After interpolating the seam, before concatenating it with the samples:
seam = seam.astype(values.dtype, copy=False)

# When configuring the VTS writer:
writer.SetDataModeToAppended()
writer.SetEncodeAppendedData(False)
writer.SetCompressorTypeToLZ4()
```

VTK exposes both controls in its [XML writer API](https://vtk.org/doc/nightly/release/9.0/html/vtkXMLWriter_8h_source.html).
The installed ParaView 5.10.1 successfully read and rendered the prototype files.
Raw appended data is supported by VTK readers, though it does not make a file
suitable for generic XML parsers; see the [VTK format documentation](https://docs.vtk.org/en/v9.6.1/vtk_file_formats/vtkxml_file_format.html).

The separate format experiments help identify which changes matter:

| VTS variant | Median reader update | Mean file size |
| --- | ---: | ---: |
| Current Float64 fields, Base64, zlib | 200 ms | 12.17 MiB |
| Current Float64 fields, raw, zlib | 111 ms | 9.13 MiB |
| Current Float64 fields, raw, LZ4 | 15 ms | 13.03 MiB |
| Original field dtypes, Base64, zlib | 178 ms | 11.27 MiB |
| Original field dtypes, raw, zlib | 89 ms | 8.46 MiB |
| Original field dtypes, raw, LZ4 | 5.9 ms | 9.75 MiB |
| Original field dtypes, raw, uncompressed | 2.7 ms | 11.46 MiB |
| Original field dtypes, radius varying fastest, raw, LZ4 | 5.3 ms | 10.89 MiB |

LZ4 is the best first choice here. Uncompressed output saves only about 3 ms of
read time while increasing the prototype's file size by approximately 18%.
Reordering the grid did not show enough reader benefit to justify making it part
of the initial fix; this experiment did not establish its downstream filter cost.

For existing processed runs, losslessly transcoding the VTS files to raw appended
LZ4 can capture most of the reader improvement without the original TIM inputs.
New processing also preserves field dtypes. Restoring Float32 rounds
only the newly interpolated boundary values to the source field's precision;
the original interior samples remain numerically identical. Regression tests cover both normal and subnormal floating-point values.

Six prototype timesteps were checked for identical coordinates, unchanged
interior sample values, matching timestamps, and exact 0°/360° copies. Boundary
rounding stayed within Float32 precision, including its subnormal limit. The
existing ParaView integration check also exercises seam slices and switching
between VTS and legacy NetCDF. No runtime seam-blending filter is needed.

Animation geometry caching is a secondary option for repeatedly playing the
same view. It trades memory for faster subsequent playback and does not eliminate
the first-pass read cost. Changing a slice or visualization can invalidate the
cached results. ParaView documents this in its [animation guide](https://docs.paraview.org/en/v5.11.2/UsersGuide/animation.html).
It was not benchmarked here. Storing a static grid separately is a larger future
format/pipeline change; the tested writer changes already close this measured gap.

The benchmark stepped through files 0000, 0001, 0002, 0080, 0081, and 0082 twice,
then reported medians from the second pass. The whole-frame measurement includes
setting animation time, querying all nine variable ranges, and an explicit render.
Reader-only measurements used the same six files, with NetCDF and current VTS
repeated at the end to check order effects. Measurements used a warm OS file cache;
the benchmark did not flush the host cache or model an S3 download. An earlier
server-initial-view test included extra visible layers and did not match the user's
view; its whole-frame results are intentionally excluded from the main comparison.

The executed whole-frame harness is preserved in
[`benchmark_vts_playback.py`](benchmark_vts_playback.py). Run it inside the server
environment with `pvpython docs/benchmark_vts_playback.py RUN_DIRECTORY slices`.
The selected timings, including per-frame samples and the reader experiments,
are in [`vts-performance-results.json`](vts-performance-results.json).
The investigation generated temporary prototype files. In the subsequent change,
the tested raw appended LZ4 encoding and preservation of field precision became
the processor defaults. Run the normal `scripts/process_output.py` command as
documented in [the processing instructions](../scripts/README.md) to generate
optimized VTS for new input. The benchmark's baseline VTS rows above refer to
the earlier Base64/zlib files, which are not automatically rewritten.
