# SCIMAP Fitted 1: Catchment Statistics

The first of three **SCIMAP Fitted** tools, numbered to be run in order. SCIMAP
Fitted infers land cover risk weights from observed water quality, rather than
assuming them as the standard [SCIMAP Sediment](04-scimap-sediment.md)/
[SCIMAP FIO](05-scimap-fio.md) tools do.

This tool delineates the catchment upstream of each water-quality monitoring
site and summarises modelled risk inside it, per land-cover class. The
resulting table is the input to
[SCIMAP Fitted 2: Calibrate Weights](12-fitted-2-calibrate-weights.md).

For every site and land-cover class it records the class area and the mean of
connectivity × erosion, plus a per-site **dilution factor** — the
rainfall-weighted contributing area at the outlet, which stands in for
discharge. Other statistics (median, min, max, standard deviation, sum) can
also be summarised, and any of them chosen as the calibration target in step 2.

**Where to find it:** Processing Toolbox → SCIMAP → **SCIMAP Fitted 1:
Catchment Statistics**; toolbar icon; `&SCIMAP` menu.

**Screenshot:**
![SCIMAP Fitted 1 Processing dialog](screenshots/11-fitted-1-catchment-statistics/dialog.png)
*The Processing dialog with the observation sites layer and its ID/value fields chosen.*

## Parameters

| Parameter | Description | Default |
|---|---|---|
| Digital Elevation Model (DEM) | The DEM to route flow over. | — |
| Observation sites | A point layer of water-quality monitoring sites. | — |
| Site ID field | Field identifying each site. | Optional |
| Observed value field(s) | One numeric field per determinand being calibrated against later. | Optional |
| Land Cover Map | The land cover raster. | — |
| Land cover classification scheme | Which land cover scheme/remap to apply (see [Apply Land Cover Risk Weights](03-apply-land-cover-risk-weights.md) for the general mechanism). | — |
| Rainfall Map | A rainfall raster. | Optional — uniform rainfall assumed if omitted |
| Connectivity raster (optional override) | Skips the connectivity solve if already available. | Optional |
| Erosion potential raster (optional override) | Skips the erosion computation if already available. | Optional |
| Rainfall-weighted contributing area (optional override) | Skips computing the dilution factor if already available. | Optional |
| Connectivity algorithm | Network Index (flow-path trace) or PDSL. | Flow-path trace |
| Erosion potential basis | Rainfall-weighted upslope area (SCIMAP-Fitted) or upslope cell area (other SCIMAP tools). | Rainfall-weighted area |
| Use stream power in erosion calculation | On by default. | On |
| Stream Initiation Threshold (m²) | Upslope area at which a channel starts. | 800,000 |
| Snap sites to | Nearest stream cell (SCIMAP-Fitted) or highest flow accumulation nearby (as in Delineate Catchment). | Nearest stream cell |
| Maximum snapping distance (map units) | How far a site may move to reach the stream network. | 500 |
| Per-class statistics to summarise (Advanced) | Which statistics to compute per class besides the mean. | Mean only |
| Drop catchment parts smaller than (Advanced) | Discards small disconnected polygon fragments. | 0 |
| Concurrent watershed delineations (Advanced) | How many site catchments to delineate at once. | 2 |
| Colour ramp | Applied to the optional hydrology raster outputs. | SCIMAP default |
| WhiteboxTools executable | Advanced; override the auto-detected binary. | Auto-detected |

## Outputs

| Output | Description |
|---|---|
| Catchment statistics table (CSV) | One row per site × land-cover class, with area, statistics of connectivity × erosion, and the dilution factor. Feeds Fitted 2. |
| Site catchments | The delineated catchment for every site, as polygons. |
| Snapped observation sites (optional) | Sites after snapping to the stream network, with `moved_m` and `fallback` attributes. |
| Connectivity / Erosion potential / Rainfall-weighted area / Breached DEM / D8 flow accumulation (all optional) | The hydrology rasters computed along the way, so [SCIMAP Fitted 3](13-fitted-3-ensemble-risk-maps.md) can reuse them rather than recomputing hydrology from scratch. |

## Notes worth knowing before reading the output

- **Erosion is normalised before any land-cover weighting** — its 5th/95th
  percentile stretch happens first, because the weighting is exactly what
  [Fitted 2](12-fitted-2-calibrate-weights.md) solves for. Applying a
  weight-dependent stretch first would make the calibration circular.
- **No two sites share a stream cell.** Two sites on one reach that collapsed
  onto the same cell would produce two identical catchments and silently
  inflate the fitted correlation, so sites are snapped to distinct cells.
- If a site's snap moves it a long way, or it ends up with an empty catchment,
  the log flags it — usually a sign the stream initiation threshold doesn't
  match that site's location.

Next: [SCIMAP Fitted 2: Calibrate Weights](12-fitted-2-calibrate-weights.md).
