# SCIMAP Fitted 3: Ensemble Risk Maps

The third of the three [SCIMAP Fitted](11-fitted-1-catchment-statistics.md)
tools. Propagates the calibrated land-cover weight sets from
[SCIMAP Fitted 2](12-fitted-2-calibrate-weights.md) back onto the raster grid
and summarises the resulting ensemble as a risk map — so the output carries its
own uncertainty rather than presenting a single weight set as *the* answer.

This is the slow step: it runs one flow-routing pass per land-cover class, per
connectivity and land-cover input combination (routing once per class rather
than once per weight set is what makes propagating dozens of weight sets
affordable). Feed it the optional raster outputs of
[SCIMAP Fitted 1](11-fitted-1-catchment-statistics.md) so it does not
recompute hydrology, and expect tens of minutes on a large grid.

**Where to find it:** Processing Toolbox → SCIMAP → **SCIMAP Fitted 3: Ensemble
Risk Maps**; toolbar icon; `&SCIMAP` menu.

**Screenshot:**
![SCIMAP Fitted 3 Processing dialog](screenshots/13-fitted-3-ensemble-risk-maps/dialog.png)
*The Processing dialog with the calibrated weights CSV and connectivity/land-cover raster lists populated.*

## Parameters

| Parameter | Description | Default |
|---|---|---|
| Calibrated weight sets (CSV) | The "Best-fitting weight sets" output of [Fitted 2](12-fitted-2-calibrate-weights.md). | — |
| Weight sets to propagate | How many of the calibrated sets to run (0 = all). | 30 |
| Connectivity raster(s) | One or more connectivity rasters. | — |
| Land cover raster(s) | One or more land cover rasters. | — |
| Land cover classification scheme | Same mechanism as elsewhere in the plugin. | — |
| Erosion potential raster (optional) | If omitted, a constant of 1 is used (risk = connectivity × land-cover weight only). | Optional |
| DEM for flow routing (breached) | The DEM used to route the propagated risk. | — |
| Breach depressions first | Tick if supplying a raw (unbreached) DEM. | Off |
| D8 flow accumulation (optional) | Reuse from Fitted 1's optional output to skip recomputing it; enables the river-network outputs. | Optional |
| Rainfall-weighted contributing area (optional) | Reuse from Fitted 1's optional output; without it, routed risk is not diluted and simply grows with catchment area. | Optional |
| Flow routing | D-infinity mass flux (SCIMAP default) or D8 mass flux (SCIMAP-Fitted research scripts). | D-infinity |
| River network threshold (km² upslope) | Defines the "in channel" cells for the network outputs. | 0.8 |
| Ignore cells below this percentile of contributing area (Advanced) | Excludes the smallest-area cells from the ensemble. | 1.0 |
| "No regrets" top tier (%) | The percentage band used for the no-regrets outputs (see below). | 5.0 |
| Concurrent flow-routing runs / row-block workers / memory budget (Advanced) | Performance tuning for large grids. | 2 / 4 / 1500 MB |
| Keep the per-class routed rasters (Advanced) | Retains intermediate per-class rasters instead of deleting them after the run. | Off |
| Colour ramp | Applied to the risk outputs. | SCIMAP default |
| WhiteboxTools executable | Advanced; override the auto-detected binary. | Auto-detected |

## Outputs

| Output | Description |
|---|---|
| Fitted risk: mean and standard deviation | Two-band raster: mean and standard deviation of routed, diluted risk across the ensemble. |
| Fitted risk: median and IQR | Two-band raster: median and inter-quartile range of the same. |
| Local (unrouted) risk: median and IQR (optional) | The same, but *before* flow routing — answers "where does the risk come from" rather than "where does it end up". |
| No-regrets priority (in channel, %) | Per cell, the percentage of weight-set combinations that place it in their own top tier — worth acting on whichever calibration turns out to be right. |
| No-regrets priority (landscape, %) | The same, for local (unrouted) risk. |
| River network risk points (optional) | Point layer along the channel network carrying mean/stdev/median/IQR — colour by `median` and drive opacity from `iqr` to see risk and confidence together. |
| Per-combination network summary (CSV) (optional) | Mean in-channel risk for every connectivity × land-cover × weight-set combination. |
| Summary histograms folder (optional) | PNG histograms of the combination and network risk distributions (needs matplotlib). |

**Screenshot:**
![Fitted risk mean/standard-deviation raster styled in QGIS](screenshots/13-fitted-3-ensemble-risk-maps/mean-stdev-map.png)
*The mean/stdev output styled in the QGIS map canvas.*

**Screenshot:**
![No-regrets priority raster styled in QGIS](screenshots/13-fitted-3-ensemble-risk-maps/no-regrets-map.png)
*A no-regrets priority raster, showing the cells worth acting on regardless of which calibrated weight set is correct.*

## Notes

- Every input raster must already share one grid — nothing is resampled here,
  because resampling would silently change the quantity the weights were
  calibrated against.
- Unlike the original SCIMAP-Fitted research scripts, this writes a styled
  point layer for the river network rather than a rendered basemap image, so
  it can be zoomed and queried over your own basemap.

This completes the SCIMAP Fitted workflow. Return to the [guide index](README.md).
