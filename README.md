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
| **Create Web Dashboard** | A shareable, offline web page of the results: zoomable 2D map, 3D terrain, charts and data downloads |
| **SCIMAP Fitted 1: Catchment Statistics** | Catchments upstream of each monitoring site, summarised per land cover class |
| **SCIMAP Fitted 2: Calibrate Weights** | Infers land cover risk weights from observed water quality, with cross-validation |
| **SCIMAP Fitted 3: Ensemble Risk Maps** | Propagates the calibrated weight sets into risk maps that carry their own uncertainty |

Hydrology runs through native WhiteboxTools calls — `BreachDepressions`,
`Slope`, `FD8FlowAccumulation`, `D8FlowAccumulation`, `D8Pointer`,
`ExtractStreams`, `RasterStreamsToVector`, `Watershed`, `DInfMassFlux`,
`DownslopeDistanceToStream` — with no GRASS or SAGA dependency. Connectivity and
the SCIMAP indices are pure NumPy, optionally accelerated by Numba.

## SCIMAP Fitted

Standard SCIMAP takes its land cover risk weights as an *assumption*. SCIMAP
Fitted *infers* them from observed water quality, which is worth doing whenever
you have monitoring data for the catchment and no strong prior on what the
weights should be.

The idea is simple. Delineate the catchment upstream of every monitoring site.
Inside each one, measure how much of each land cover class there is and how
connected and erodible that ground is. Then search for the set of class weights
whose predicted risk ranks the sites in the same order the observations do.

Run the three tools in order.

**1. Catchment Statistics** takes a DEM, a land cover raster, rainfall and a
point layer of monitoring sites, and writes one row per site per land cover
class: the class area, the mean of connectivity × erosion, and a per-site
dilution factor (the rainfall-weighted contributing area at the outlet, standing
in for discharge). Observed values can travel in attribute fields of the point
layer.

**2. Calibrate Weights** draws thousands of candidate weight vectors by Latin
hypercube and scores each by Spearman rank correlation against the observations.
It writes every set's metrics, the best-fitting sets, diagnostic plots, and a
parameter set XML that loads straight into **Apply Land Cover Risk Weights** or
**SCIMAP Sediment**. It reads its input from step 1, or from an
`all_catchments_class_means_summary.csv` produced by the SCIMAP-Fitted research
scripts.

**3. Ensemble Risk Maps** propagates the best weight sets back onto the grid,
producing mean/standard deviation and median/IQR rasters plus two "no regrets"
maps showing, per cell, the percentage of weight sets that put it in their top
tier. Those last two are the ones to target work from: a cell scoring high is
worth acting on whichever calibration turns out to be right.

### Three things worth knowing

**Only the ratios between weights are identifiable.** Multiplying every weight
by the same number leaves the rank correlation unchanged, and SCIMAP Standard's
own percentile stretch removes it again downstream, so the absolute numbers
carry no information. Reported weights are rescaled so the largest is 1. Without
that the top-N boxplots would mostly show the spread of an arbitrary scale
rather than real uncertainty.

**Read the dotty plot before trusting a weight.** A class whose points form a
clear ridge is constrained by the data. A class whose points are a flat band is
not, however tight its box looks.

**Cross-validate if you can.** With seven classes and thirty sites there are
roughly four observations per free parameter, which is few enough that a high
correlation can be an artefact of searching a large space. Cross-validation
refits on all but *k* sites and predicts those held out; if that score is much
lower than the calibration score, the fit is not real. It is off by default
because its cost grows sharply with *k* — leave-one-out over thirty sites is
seconds, leave-five-out is hours.

### Choose the land cover scheme explicitly

UKCEH changed its target class list at LCM2015. In the 21-class scheme classes
20 and 21 are Urban and Suburban; in the older 23-class scheme they are Littoral
sediment and Saltmarsh. Reading a 2015-or-later raster with the older table
therefore maps every urban cell to SCIMAP class 7 (Other) without warning,
because those IDs exist in both tables. The Fitted tools ask which scheme you
are using and default to the modern one.

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
| Create Web Dashboard | A folder containing `index.html`, map tiles, data and downloads; optionally a zip of the lot |

## Web dashboard

**Create Web Dashboard** turns a finished run into a folder you can hand to
anyone. Open `index.html` by double-clicking it: there is no web server to
start, no internet connection needed and no GIS software required.

It gives you:

- a **2D map** over shaded relief generated from your own DEM, with the result
  layers as zoomable tiles, per-layer opacity, and the stream network coloured
  by in-channel risk;
- a choice of **background map** — the shaded relief, Esri Street Map, Esri
  Dark Gray or Esri Satellite. The relief is built into the folder and is what
  the dashboard opens with, so the map still works with no connection; the
  three Esri basemaps are fetched live and are there for context when the
  viewer is online. Turn them off with *Offer online basemaps* if the dashboard
  is for somewhere without internet access, or if you would rather it made no
  network requests;
- a **3D terrain view** with the selected result draped over the DEM, the
  stream network lifted onto the surface, water animated downstream and
  coloured by the in-channel risk of the reach it runs along, an optional slow
  automatic orbit, and controls for vertical exaggeration and sun position;
- **hover anywhere** to read every layer's value, the elevation and the
  coordinates; click a reach for its risk, rank and length;
- **charts**: the distribution of each layer; a risk-concentration curve over
  the whole catchment that answers "how much of the land produces how much of
  the risk?" — with the highest-risk 1/5/10/25/50% of the ground against the
  risk it carries, and the area you would have to treat to capture 50/80/95% of
  the risk; the wetness-connectivity curve; and a ranked table of priority
  reaches that zoom the map when you pick one;
- **map images**: save the 2D view as a PNG at screen (1600 px) or print
  (4000 px) size, with a scale bar, drawn from the embedded data with whatever
  layers, opacities and stream colours you have set;
- **3D model export**: save the catchment as `.glb` (keeps the draped colours,
  opens in Blender or any glTF viewer), `.stl` or `.obj`. Every format is a
  closed, watertight solid — surface, sides and a flat base — so it can be
  3D printed without repairing the mesh first. Set the printed size with the
  slider (200 mm by default); the model is scaled to fit that box, centred and
  standing on zero, and the panel shows the finished dimensions and the map
  scale. STL and OBJ come out in millimetres, glTF in metres, each being the
  unit those formats are read in;
- **downloads**: your full-resolution results in their original projection,
  plus `provenance.json` and SHA-256 checksums;
- **provenance**: the parameters, risk weights, input layers, CRS and software
  versions behind the run;
- **Print / Save as PDF**, which lays the whole thing out as a report.

Build one from the **Results** tab of the SCIMAP panel, from the Processing
toolbox, or automatically after every run by ticking *Build one after each run*.

Two settings control size. *Detail of the embedded data* (default 1024 px) sets
the resolution of the values behind the 3D view, the hover readout and the
charts. Map tiles are written up to the resolution of your own data and no
further — zooming past that stretches the last level rather than storing an
upscale of pixels the analysis never resolved. A typical 5 m catchment produces
a few hundred tiles per layer.

The dashboard carries its own copies of Leaflet and three.js, so it will still
work years from now with no network. The 3D view needs WebGL; everything else
works without it.

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

The web dashboard needs nothing beyond NumPy and GDAL, both of which ship with
QGIS. It does not use matplotlib.

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
