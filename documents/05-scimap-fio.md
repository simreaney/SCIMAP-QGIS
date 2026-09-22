# SCIMAP FIO

Maps faecal indicator organism (FIO) delivery risk from a DEM, an FIO
concentration raster and a rainfall map. The hydrology is identical to
[SCIMAP Sediment](04-scimap-sediment.md); the difference is that the risk
weighting comes from an FIO concentration raster (CFU) divided by a
normalisation constant, instead of from land-cover weights.

The SCIMAP web application picks a season and looks up its own bundled
`<season>_fio.tif` and `<season>_rain.tif` national rasters. Since the plugin
has no bundled national dataset, it asks for those two rasters directly —
choose the FIO/rainfall pair for the season being mapped.

**Where to find it:** Processing Toolbox → SCIMAP → **SCIMAP FIO**; toolbar
icon ("Run SCIMAP FIO"); `&SCIMAP` menu; or the [SCIMAP Panel](01-scimap-panel.md)'s
Run tab with **Mapping type** set to SCIMAP FIO.

**Screenshot:**
![SCIMAP FIO Processing dialog](screenshots/05-scimap-fio/dialog.png)
*The full Processing dialog.*

## Parameters

| Parameter | Description | Default |
|---|---|---|
| Digital Elevation Model (DEM) | The DEM to route flow over. | — |
| FIO Concentration Raster (CFU) | Seasonal FIO concentration raster. | — |
| FIO normalisation constant (CFU) | Divides the FIO raster before it is used as the risk weighting. | Plugin default |
| Rainfall Map | Rainfall raster for the same season as the FIO raster. | — |
| Stream Initiation Threshold (m²) | Upslope area at which a channel starts. | 800,000 |
| Use stream power in erosion calculation | Include slope × contributing area in the erosion term. | On |
| Connectivity algorithm | Network Index (flow-path trace) or Percentage Downslope Saturated Length (PDSL). | Flow-path trace |
| Colour ramp | Applied to the output rasters. | SCIMAP default |
| WhiteboxTools executable | Advanced; override the auto-detected binary. | Auto-detected |

## Outputs

| Output | Description |
|---|---|
| Network Connectivity Risk | How readily runoff from each cell reaches the stream network. |
| FIO Delivery Risk | The erosion-risk output, here labelled for FIO delivery (percentile-stretched). |
| Instream Risk Concentration | The stream network, attributed with a `Risk` field. |
| Stream Risk Points (optional) | One point per valid raster cell at or above the stream threshold, with `scimap_risk`. |
| Stream Network (KML) (optional) | The stream network exported to KML. |

## Notes

- Choose the FIO and rainfall raster pair that matches the season being
  investigated — the two rasters must correspond to each other.
- Everything else (stream extraction, connectivity, combination and export)
  works exactly as in SCIMAP Sediment; see that page for the underlying
  pipeline.

Next: [Network Index](06-network-index.md).
