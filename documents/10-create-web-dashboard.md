# Create Web Dashboard

Builds a self-contained web dashboard from SCIMAP results: a zoomable 2D map, a
3D terrain view with the results draped over it, interactive charts, run
provenance and a download bundle.

The output folder opens by double-clicking `index.html`. It needs no web
server, no internet connection and no GIS software, so it can be zipped and
sent to anyone — a colleague, a landowner, a funder — to explore the results
themselves.

**Where to find it:** Processing Toolbox → SCIMAP → Export → **Create Web
Dashboard**; toolbar icon; `&SCIMAP` menu; or the
[SCIMAP Panel](01-scimap-panel.md)'s Results tab, via **Build web dashboard…**
(or automatically after each run, if **Build one after each run** is ticked).

**Screenshot:**
![Create Web Dashboard Processing dialog](screenshots/10-create-web-dashboard/dialog.png)
*The Processing dialog, scrolled to show the named result-layer slots.*

## Parameters

Every input except the dashboard folder is optional — supply whichever results
are available and leave the rest blank.

| Parameter | Description |
|---|---|
| Digital elevation model | Provides the shaded-relief base map and the 3D terrain. Without it, the dashboard has no relief base map and no 3D view. |
| In-channel risk / Erosion risk / Network connectivity / Network index / Flood risk (mean) / Flood risk (variability) / Land cover risk weighting | Named raster slots — each result raster is offered its own labelled input, so it arrives on the dashboard with the right title, colour ramp and plain-English description rather than a raw layer name. |
| Additional raster layers | Any other rasters to include, labelled with their own layer name. |
| Stream network (with a Risk field) / Catchment boundary / Stream risk points | Named vector slots for the corresponding SCIMAP outputs. |
| Wetness-connectivity curve dataset (CSV) | The dataset from [Network Index](06-network-index.md), if produced. |
| Dashboard title / Subtitle / Organisation | Free text shown on the dashboard. |
| Detail of the embedded data | Standard (512px) / High (1024px) / Maximum (2048px) — resolution of the data baked into the 2D map. |
| Detail of the 3D terrain mesh | Low (256) / Standard (512) / High (1024). |
| Maximum map zoom level | 0 matches the data resolution automatically. |
| Initial vertical exaggeration in the 3D view | 1.0–6.0. |
| Include the 3D terrain view | On by default. |
| Include map tiles (needed for the 2D map) | On by default. |
| Include the source data for download | Bundles the underlying files for download from the dashboard. On by default. |
| Offer online basemaps when the viewer has an internet connection | Adds Esri Street Map, Esri Dark Gray and Esri Satellite as options alongside the offline shaded relief, which stays the default so an offline viewer sees the same thing either way. |
| Also write a zip of the whole dashboard | On by default — convenient for emailing or uploading. |

## Outputs

| Output | Description |
|---|---|
| Dashboard folder | The folder containing `index.html` and all assets. |
| Dashboard home page | Path to `index.html` itself. |

## Screenshots of the dashboard itself

**Screenshot:**
![The dashboard's 2D map view](screenshots/10-create-web-dashboard/dashboard-2d-map.png)
*The dashboard's 2D map, open in a browser, with the layer list and a result layer switched on.*

**Screenshot:**
![The dashboard's 3D terrain view](screenshots/10-create-web-dashboard/dashboard-3d-view.png)
*The 3D terrain view with a risk layer draped over it.*

**Screenshot:**
![The dashboard's charts](screenshots/10-create-web-dashboard/dashboard-charts.png)
*One of the dashboard's interactive charts (e.g. the risk-concentration chart).*

## Notes

- Point this tool at whichever outputs exist for a given run — nothing is
  required beyond the destination folder, though a DEM is strongly recommended
  for a useful dashboard.
- The dashboard can also export the 2D map as a PNG and the 3D terrain as
  glTF/STL/OBJ from within the dashboard itself, once opened.

Next: [SCIMAP Fitted 1: Catchment Statistics](11-fitted-1-catchment-statistics.md).
