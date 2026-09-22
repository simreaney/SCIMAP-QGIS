# Export SCIMAP Results

Converts a finished SCIMAP result into the formats the SCIMAP web application
offers for download: a stream-risk point layer and Cloud-Optimised GeoTIFF from
a result raster, and GeoPackage / KML from a result vector.

**Where to find it:** Processing Toolbox → SCIMAP → Export → **Export SCIMAP
Results**; or the [SCIMAP Panel](01-scimap-panel.md)'s Results tab, via the
**Export…** button. There is no toolbar icon for this tool.

**Screenshot:**
![Export SCIMAP Results Processing dialog](screenshots/09-export-scimap-results/dialog.png)
*The Processing dialog with raster and vector inputs chosen and all four output formats enabled.*

## Parameters

| Parameter | Description | Default |
|---|---|---|
| SCIMAP Result Raster | A result raster (e.g. Erosion Risk, In-Channel Risk). Required for the point and COG outputs. | Optional |
| SCIMAP Result Vector | A result vector (e.g. Instream Risk Concentration). Required for the GeoPackage output, and for KML if no points output is requested instead. | Optional |
| Field name for exported point values | The attribute field written on the exported points. | `scimap_risk` |
| Export only cells with values greater than zero | Skips zero/negative cells when converting the raster to points. | Off |

Leave any output destination blank to skip it — at least one output format
must be chosen.

## Outputs

| Output | Description |
|---|---|
| Result as Points (optional) | One point per raster cell, with the value in the named field. |
| Result as GeoPackage (optional) | The result vector copied to GeoPackage. |
| Result as KML (optional) | The result vector (or the points output, if no vector was supplied) exported to KML. |
| Result as Cloud-Optimised GeoTIFF (optional) | The result raster, written as a COG. |

## Notes

- This tool is a pure format conversion step — it does no recomputation, so it
  can be run against results from any of the plugin's risk-mapping tools.
- Matches the download menu of the SCIMAP web application, so results produced
  in QGIS can be shared in formats web-application users expect.

Next: [Create Web Dashboard](10-create-web-dashboard.md).
