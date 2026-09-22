# SCIMAP Sediment

Maps fine sediment and diffuse pollution risk from a DEM, a land cover map and
a rainfall map — the core SCIMAP model, ported from the SCIMAP web
application's `scimap_standard.py`.

By default the land cover raster is reclassified using the SCIMAP class remap
and risk weight tables (the same tables as
[Apply Land Cover Risk Weights](03-apply-land-cover-risk-weights.md) and the
[SCIMAP Panel](01-scimap-panel.md)'s Parameters tab). Tick **Land cover is
already a risk weighting** to pass an already-weighted raster through
untouched instead.

**Where to find it:** Processing Toolbox → SCIMAP → **SCIMAP Sediment**;
toolbar icon ("Run SCIMAP Sediment"); `&SCIMAP` menu; or the
[SCIMAP Panel](01-scimap-panel.md)'s Run tab with **Mapping type** set to
SCIMAP Sediment.

**Screenshot:**
![SCIMAP Sediment Processing dialog](screenshots/04-scimap-sediment/dialog.png)
*The full Processing dialog, scrolled to show the risk-weight table and stream/connectivity options.*

## Parameters

| Parameter | Description | Default |
|---|---|---|
| Digital Elevation Model (DEM) | The DEM to route flow over. | — |
| Land Cover Map / Risk Weighting | Land cover raster, or an already-weighted raster (see below). | — |
| Land cover is already a risk weighting | Skip reclassification and use the input raster's values directly. | Off |
| Land cover already uses SCIMAP classes (1-7) | Skip the land-cover-ID remap step; input is already coded 1–7. | Off |
| Land cover class → SCIMAP class | Remap matrix, pre-filled with the CEH Land Cover Map mapping. | CEH mapping |
| SCIMAP class → risk weight | Weight matrix (0–1000) per SCIMAP class. | SCIMAP defaults |
| Parameter set XML | Overrides the weight table above; the same XML format used across the plugin and the web application. | Optional |
| SCIMAP class used for unmapped land cover values | Fallback class. | 6 (Other) |
| Rainfall Map | A rainfall raster. | — |
| Stream Initiation Threshold (m²) | Upslope area at which a channel starts. | 800,000 |
| Use stream power in erosion calculation | Include slope × contributing area (stream power) in the erosion term. | On |
| Connectivity algorithm | Network Index (flow-path trace) or Percentage Downslope Saturated Length (PDSL). | Flow-path trace |
| Colour ramp | Applied to the output rasters. | SCIMAP default |
| WhiteboxTools executable | Advanced; override the auto-detected binary. | Auto-detected |

## Outputs

| Output | Description |
|---|---|
| Network Connectivity Risk | How readily runoff from each cell reaches the stream network. |
| Erosion Risk | Where sediment is most likely to be mobilised (slope, flow accumulation and land-cover risk weight combined, percentile-stretched). |
| Instream Risk Concentration | The stream network as a line layer, attributed with a `Risk` field. |
| Stream Risk Points (optional) | One point per valid raster cell at or above the stream threshold, with a `scimap_risk` field — matches the SCIMAP web application's point download. |
| Stream Network (KML) (optional) | The stream network exported to KML. |
| SCIMAP Land Cover Classes (optional) | The reclassified 1–7 land cover raster, useful for checking the remap. |

## How it works

1. Breach DEM depressions, compute D8/FD8 flow direction and accumulation, and
   extract the stream network from the threshold.
2. Reclassify land cover into SCIMAP risk weights (unless already weighted).
3. Compute erosion risk from slope, contributing area and the risk weight, then
   stretch it to 0–1 by its 5th/95th percentiles.
4. Compute network connectivity with the chosen algorithm.
5. Route erosion × connectivity across the catchment (DInfMassFlux) and divide
   by rainfall-weighted contributing area to get the in-channel risk
   concentration.
6. Write the rasters, vectorise the stream network with its `Risk` attribute,
   and optionally export stream risk points and KML.

Next: [SCIMAP FIO](05-scimap-fio.md).
