# SCIMAP Sediment QGIS Plugin

This plugin adds the SCIMAP Sediment diffuse pollution risk workflow to QGIS Processing.

It calculates:
- Erosion Risk (raster)
- Network Connectivity Risk (raster)
- In-Channel Risk (raster)
- Vector Stream Network (vector)

The algorithm uses native WhiteboxTools calls for hydrological operations, including:
- BreachDepressions
- Slope
- DInfFlowAccumulation
- D8Pointer
- DInfMassFlux
- ExtractStreams
- RasterStreamsToVector

## What The Plugin Does

The plugin runs a SCIMAP-style source-to-stream routing workflow from three raster inputs:
- Digital Elevation Model (DEM)
- Land Cover risk weighting raster
- Rainfall raster

High-level process:
1. Fill/breach DEM depressions.
2. Compute slope, D-Infinity flow accumulation, and D8 flow directions.
3. Compute erosion risk.
4. Compute network connectivity.
5. Route rainfall-weighted area and routed source risk.
6. Compute in-channel risk as routed risk divided by routed rainfall-weighted area.
7. Extract and vectorize stream network using a stream initiation threshold.

## Requirements

- QGIS 3.28 to 4.x (tested on QGIS 4.0.1)
- WhiteboxTools executable available on your system
- Input rasters must be aligned (same extent, resolution, and CRS)

WhiteboxTools executable examples:
- macOS/Linux: `whitebox_tools` (no extension)
- Windows: `whitebox_tools.exe`

## Installation

### Option 1: Install from ZIP (recommended for normal use)

1. Package the plugin folder so the ZIP root contains files like:
   - `metadata.txt`
   - `__init__.py`
   - `scimap_algorithm.py`
   - `scimap_provider.py`
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

## How To Use

You can run the algorithm from:
- Toolbar button: Run SCIMAP Standard
- Plugins menu: SCIMAP > Run SCIMAP Standard
- Processing Toolbox: SCIMAP > SCIMAP Sediment Diffuse Pollution Risk

### Parameters

- Digital Elevation Model (DEM): elevation raster
- Land Cover Map / Risk Weighting: risk weighting raster
- Rainfall Map: rainfall raster
- Stream Initiation Threshold (m2): contributing area threshold for stream extraction (default: 800000)
- WhiteboxTools executable (optional): path to `whitebox_tools` binary

If WhiteboxTools path is supplied, the plugin stores it in QGIS settings and reuses it on later runs.

### Outputs

- Network Connectivity Risk (raster)
- Erosion Risk (raster)
- In-Channel Risk (raster)
- Vector Stream Network (vector)

## Tips

- Choose a stream threshold appropriate for raster resolution. The threshold is area-based and is converted internally to cell count.
- For very high-resolution DEMs, runtime can be long.
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
Risk-based modelling of diffuse land use impacts from rural landscapes upon salmonid fry abundance.
Ecological Modelling, 222(4), 1016-1029.
https://doi.org/10.1016/j.ecolmodel.2010.08.022
