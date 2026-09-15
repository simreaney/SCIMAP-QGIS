# SCIMAP Toolkit QGIS Plugin

A native QGIS companion to the SCIMAP web application. It brings the SCIMAP
catchment, sediment, FIO, network index and flood workflows into QGIS
Processing, plus a guided panel that mirrors the web app's step-by-step
workflow.

Everything runs on layers you supply — there is no bundled national dataset and
no server, so the plugin works anywhere in the world.

## Tools

| Tool | Purpose |
|---|---|
| **SCIMAP Sediment** | Fine sediment / diffuse pollution risk from DEM, land cover and rainfall |
| **SCIMAP FIO** | Faecal indicator organism delivery risk from DEM, an FIO concentration raster and rainfall |
| **Delineate Catchment** | Catchment boundary upstream of a pour point, with snapping to the channel network |
| **Apply Land Cover Risk Weights** | Reclassify a land cover raster into SCIMAP risk weights |
| **Network Index** | Connectivity from a DEM alone, using uniform rainfall |
| **SCIMAP Flood** | Flood risk mean/stdev from pre-computed connectivity, runoff, rainfall and overland flow distance rasters |
| **Overland Flow Distance to Point** | Downslope flow-path distance to the nearest target point |
| **Export SCIMAP Results** | GeoPackage, KML, point vector and Cloud-Optimised GeoTIFF exports |

Hydrology runs through native WhiteboxTools calls — `BreachDepressions`,
`Slope`, `FD8FlowAccumulation`, `D8FlowAccumulation`, `D8Pointer`,
`ExtractStreams`, `RasterStreamsToVector`, `Watershed`, `DInfMassFlux`,
`DownslopeDistanceToStream` — with no GRASS or SAGA dependency. Connectivity and
the SCIMAP indices are pure NumPy, optionally accelerated by Numba.

## The SCIMAP panel

Open it from the SCIMAP toolbar button or **Plugins → SCIMAP → SCIMAP Panel**.
Its five tabs follow the web application's workflow:

- **Catchment** — pick a DEM, click a pour point on the map, delineate.
- **Parameters** — edit the risk weight for each of the seven SCIMAP land cover
  classes; import and export parameter sets as XML, interchangeable with the web
  application's Parameters workspace.
- **Run** — choose Sediment or FIO, pick your layers, optionally clip the DEM,
  land cover/FIO and rainfall layers to a catchment boundary, run. Delineating a
  catchment on the Catchment tab fills this in automatically.
- **Flood** — click impact points on the map, turn them into overland flow
  distance rasters, then run SCIMAP Flood.
- **Results** — session run history, live colour-ramp switching, exports, and the
  WhiteboxTools executable setting.

Runs are cancellable, and progress and log messages appear at the bottom of the
panel. Everything the panel does is also available as a Processing algorithm, so
it all works in batch mode and inside Processing models.

## Land cover risk weighting

The web application holds its risk weights in a Parameters workspace; the plugin
carries the same tables, so you do **not** need to arrive with a pre-weighted
raster.

1. Raw land cover IDs are mapped to SCIMAP classes 1–7 using the remap table,
   pre-filled with the CEH Land Cover Map mapping (LCM 1–23 → SCIMAP 1–7).
2. Each SCIMAP class is given a risk weight, defaulting to the SCIMAP values
   (Woodland 0.2, Arable 1.0, Improved Grassland 0.3, Extensive Grassland 0.15,
   Moorland 0.3, Urban 0.5, Other 0.5).
3. Values present in the data but absent from the weight table are backfilled
   with the fallback class's weight (class 7 by default), so there are no
   internal NoData holes. A warning lists the unmapped IDs.

Both tables are editable per run. If your raster already uses SCIMAP classes,
tick **Land cover already uses SCIMAP classes (1-7)** to skip the remap. If it is
already a risk weighting, tick **Land cover is already a risk weighting** and it
is used as-is — the behaviour of plugin versions before 2.0.

A **Parameter set XML** file overrides the weight table, and accepts both
SCIMAP-class and legacy CEH-class XML exported from the web application.

## How the risk mapping works

SCIMAP Sediment and SCIMAP FIO run the same pipeline, differing only in where the
per-cell risk weight comes from:

1. Breach DEM depressions.
2. Compute slope, FD8 flow accumulation and D8 flow directions; extract the
   stream network.
3. Derive the risk weight — land cover reclassification (Sediment) or FIO
   concentration divided by the normalisation constant (FIO).
4. Erosion risk = `|accumulation| x cell area x tan(slope) x risk weight`,
   normalised between its 5th and 95th percentiles.
5. Connectivity via flow-path trace: each cell takes the minimum topographic
   wetness index along its downstream path to the channel network.
6. Risk concentration = accumulated risk (erosion x connectivity, routed across
   the *whole* catchment with WhiteboxTools `DInfMassFlux`) divided by the
   rainfall-weighted catchment area (routed the same way), matching the SCIMAP
   web application. This is computed for every cell in the catchment, not just
   the channel network.
7. Vectorise the stream network and attach the mean risk concentration along
   each reach as a `Risk` field — the **Instream Risk Concentration** output.
   A separate, coarser cut of the same ratio (cells at or above the stream
   initiation threshold) feeds the optional stream risk points.

Both the risk loading and the rainfall loading are routed downslope with
WhiteboxTools `DInfMassFlux`; if that routing fails the run falls back to a
local-scaling approximation (`contributing area x scaled value`) with a
warning.

## Inputs and alignment

Every input is an ordinary QGIS raster or vector layer. Land cover, rainfall and
FIO rasters no longer need to be pre-aligned to the DEM — anything on a different
grid is resampled onto the DEM before analysis, and a warning says so. The DEM
defines the output grid, extent and CRS.

The Flood and Overland Flow Distance tools still require their pre-computed
inputs to share an identical grid, and will refuse to run otherwise.

## Outputs and styling

Results are styled automatically with the web application's colour ramps,
stretched between the 5th and 95th percentiles: Magma for erosion, Viridis for
connectivity, Plasma for instream risk concentration and stream risk points,
Spectral for flood. Pick a single ramp for every layer with the **Colour ramp**
parameter, or switch ramps after the fact in the panel's Results tab.

| Tool | Outputs |
|---|---|
| Sediment / FIO | Erosion risk, connectivity, instream risk concentration network; optional stream risk points, KML and SCIMAP class raster |
| Delineate Catchment | Catchment boundary; optional snapped pour point and basin raster |
| Apply Land Cover Risk Weights | Risk weighting raster; optional SCIMAP class raster |
| Network Index | Network index raster |
| SCIMAP Flood | Flood mean and standard deviation rasters |
| Overland Flow Distance | Travel-distance raster |

## Requirements

- QGIS 3.28 or newer (tested on 3.42 and 4.x)
- The WhiteboxTools executable (`whitebox_tools`, or `whitebox_tools.exe` on
  Windows) available on your system

Set the executable once in the panel's Settings section, or per run with the
**WhiteboxTools executable** parameter; either way it is stored in QGIS settings
and reused. If it is on your `PATH`, or the `wbt_for_qgis` plugin is installed,
it is found automatically.

Numba is optional. Without it, connectivity falls back to pure NumPy, which is
correct but noticeably slower on large rasters.

## Installation

### Install from ZIP (recommended)

1. Zip the `qgis_plugin` folder so the archive root contains `metadata.txt`,
   `__init__.py`, `scimap_provider.py` and the `core/`, `algorithms/`, `data/`,
   `gui/` and `icons/` directories.
2. **Plugins → Manage and Install Plugins… → Install from ZIP**.
3. Select the ZIP and install, then enable the plugin if prompted.

### Install as a development folder

Copy the `qgis_plugin` directory into your QGIS profile plugins directory and
restart QGIS:

- macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
- Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
- Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`

(Substitute `QGIS4` for a QGIS 4 profile.)

## Tips

- The stream initiation threshold is area-based and converted internally to a
  cell count, so pick a value appropriate to your raster resolution. If the
  stream network looks too sparse or too dense, tune this first.
- Catchment delineation snaps the pour point to the nearest cell with a large
  enough contributing area. If it snaps to the wrong tributary, reduce the snap
  search radius; if it does not snap at all, raise it or lower the minimum
  contributing area.
- High-resolution DEMs take a long time. Clip to your area of interest first.

## Troubleshooting

- **WhiteboxTools not found** — set the executable in the panel's Settings, or
  with the per-run parameter. On macOS and Linux the file has no extension, so
  use the file dialog's *All files* filter to select `whitebox_tools` directly.
- **Connectivity is very slow** — Numba is not available in your QGIS Python. The
  run log says which backend is in use.
- **Flood or Overland Flow Distance rejects the inputs** — those tools require
  every raster on an identical grid. Warp them to a common grid first.
- **The panel greys out during a run** — the algorithms drive GDAL and
  WhiteboxTools directly and so run on the main thread. Progress still updates
  and Cancel still works.

## Upgrading from 1.x

- `scimap_algorithm.py` still exports every algorithm class, so existing scripts
  and saved models keep working. New code should import from the `algorithms`
  and `core` packages.
- Sediment now reclassifies land cover by default. To keep 1.x behaviour, tick
  **Land cover is already a risk weighting**.
- The plugin no longer imports anything from the SCIMAP web application. Earlier
  versions silently used the web app's connectivity implementation when run from
  inside the `scimap-app` repository, which could give different results to a
  distributed ZIP; only the plugin's own implementation is used now.

## Citation

Reaney, S., Lane, S., Heathwaite, A., and Dugdale, L. (2011).
Risk-based modelling of diffuse land use impacts from rural landscapes upon
salmonid fry abundance. *Ecological Modelling*, 222(4), 1016-1029.
https://doi.org/10.1016/j.ecolmodel.2010.08.022

SCIMAP-Flood: Reaney, S. M. (2022). Spatial targeting of nature-based solutions
for flood risk management within river catchments.
*Journal of Flood Risk Management*, e12803.
