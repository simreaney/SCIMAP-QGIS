# SCIMAP Toolkit QGIS Plugin

This plugin provides the SCIMAP sediment, network index, and flood workflows inside QGIS Processing.
It is a native QGIS companion to the SCIMAP web app and legacy flood workflow.

It calculates:
- Erosion Risk (raster)
- Network Connectivity Risk (raster)
- In-Channel Risk (raster)
- Vector Stream Network (vector)
- SCIMAP Flood Mean (raster)
- SCIMAP Flood Standard Deviation (raster)

The algorithm uses native WhiteboxTools calls for hydrological operations, including:
- BreachDepressions
- Slope
- FD8FlowAccumulation
- D8Pointer
- ExtractStreams
- RasterStreamsToVector
- DownslopeDistanceToStream

The plugin also provides separate tools:
- SCIMAP Network Index from DEM (raster output)
- SCIMAP Flood from pre-computed connectivity, runoff, rainfall, and overland flow distance rasters

Flood outputs are the mean and standard deviation rasters.

## What The Plugin Does

The sediment workflow runs a SCIMAP-style source-to-stream routing workflow from three raster inputs:
- Digital Elevation Model (DEM)
- Land Cover risk weighting raster
- Rainfall raster

High-level process:
1. Fill/breach DEM depressions.
2. Compute slope, FD8 flow accumulation, and D8 flow directions.
3. Compute erosion risk.
4. Compute network connectivity using flow-path trace.
5. Compute FD8-based rainfall-weighted and routed source-risk proxies.
6. Compute in-channel risk as routed risk divided by routed rainfall-weighted area.
7. Extract and vectorize stream network using a stream initiation threshold.

Network Index tool process:
1. Fill/breach DEM depressions.
2. Compute slope, FD8 flow accumulation, and D8 flow directions.
3. Compute network index using flow-path trace connectivity.
4. Write network index raster.

Flood tool process (mirrors pySCIMAP-Flood_2026.py, operating on pre-computed inputs rather than deriving them from a DEM):
1. Load a pre-computed connectivity raster and a runoff / land-cover weights raster.
2. Load all supplied rainfall pattern rasters (there is no Top-N selection step; every rainfall raster you provide is used).
3. Load all supplied overland flow distance rasters.
4. For each rainfall x overland-flow-distance combination, compute `normalise(rainfall) x (normalise(OFD) + 1.0)`, multiplied by connectivity x normalised runoff.
5. Write the mean and standard deviation across all combinations.

## Tool Summary

Use the plugin when you want:

- Sediment mapping from DEM, land cover, and rainfall rasters.
- A standalone network index from a DEM.
- Flood risk mapping from pre-computed connectivity, runoff, rainfall, and overland flow distance rasters.
- A standalone overland flow travel-distance raster to one or more points, for use as a Flood tool input or on its own.

## Requirements

- QGIS 3.28 to 4.x (tested on QGIS 4.0.1)
- WhiteboxTools executable available on your system
- Input rasters must be aligned (same extent, resolution, and CRS)

WhiteboxTools executable examples:
- macOS/Linux: `whitebox_tools` (no extension)
- Windows: `whitebox_tools.exe`

## Installation

### Option 1: Install from ZIP (recommended for normal use)

1. Download this repo as a zip file. 
2. In QGIS, go to Plugins > Manage and Install Plugins....
3. Choose Install from ZIP.
4. Select the plugin ZIP and install.
5. Enable the plugin if prompted.

### Option 2: Install as a development plugin folder

1. Copy the `qgis_plugin` directory into your QGIS profile plugins directory.
2. Rename that copied folder to your plugin package name if needed.
3. Restart QGIS.
4. Open Plugins > Manage and Install Plugins... and enable SCIMAP Sediment.

Typical profile plugin locations:
- macOS: `~/Library/Application Support/QGIS/QGIS4/profiles/default/python/plugins/`
- Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/` (or QGIS4 profile)
- Windows: `%APPDATA%\\QGIS\\QGIS3\\profiles\\default\\python\\plugins\\` (or QGIS4 profile)

### Option 3: Install from the QGISm plugin directory
- Current pending review

## How To Use

You can run the algorithm from:
- Toolbar button: Run SCIMAP Standard
- Plugins menu: SCIMAP > Run SCIMAP Standard
- Processing Toolbox: SCIMAP > SCIMAP Sediment Diffuse Pollution Risk

You can also run the standalone network tool from:
- Processing Toolbox: SCIMAP > SCIMAP Network Index from DEM

You can run the flood tool from:
- Toolbar button: Run SCIMAP Flood
- Plugins menu: SCIMAP > Run SCIMAP Flood
- Processing Toolbox: SCIMAP > SCIMAP Flood

### Parameters

- Digital Elevation Model (DEM): elevation raster
- Land Cover Map / Risk Weighting: risk weighting raster
- Rainfall Map: rainfall raster
- Stream Initiation Threshold (m2): contributing area threshold for stream extraction (default: 800000)
- Flow routing is fixed to FD8 (no D-Infinity option).
- Connectivity is fixed to flow-path trace (Topological Netwet option removed).
- WhiteboxTools executable (optional): path to `whitebox_tools` binary

Flood tool parameters:
- Connectivity Raster (pre-computed)
- Runoff / Land Cover Weights Raster (pre-computed)
- Rainfall Pattern Rasters: all supplied rasters are used (no Top-N selection)
- Overland Flow Distance Rasters (pre-computed): one per impact point or scenario; generate these with the Overland Flow Distance to Point tool

If WhiteboxTools path is supplied, the plugin stores it in QGIS settings and reuses it on later runs.

### Outputs

- Network Connectivity Risk (raster)
- Erosion Risk (raster)
- In-Channel Risk (raster)
- Vector Stream Network (vector)

Standalone Network Index tool output:
- Network Index (raster)

Flood tool outputs:
- SCIMAP-Flood Mean (raster)
- SCIMAP-Flood Standard Deviation (raster)

## Tips

- Choose a stream threshold appropriate for raster resolution. The threshold is area-based and is converted internally to cell count.
- For very high-resolution DEMs, runtime can be long. Minutes to hours for large catchmnts (2000 km2 plus)
- If outputs look sparse or too dense, tune Stream Initiation Threshold first.

## Troubleshooting

- WhiteboxTools not found:
  - Set the WhiteboxTools executable parameter explicitly.
  - Confirm the file exists and is executable.
- WhiteboxTools executable appears unselectable in file dialog:
  - Use the plugin's executable selector with All files and select `whitebox_tools` directly.
- Processing fails with mismatched raster dimensions:
  - Reproject/resample inputs so DEM, land cover, and rainfall are aligned.

## Citation

Reaney, S., Lane, S., Heathwaite, A., and Dugdale, L. (2011).
Risk-based modelling of diffuse land use impacts from rural landscapes upon salmonid fry abundance. _Ecological Modelling_, 222(4), 1016-1029. https://doi.org/10.1016/j.ecolmodel.2010.08.022
