# Network Index

Computes a SCIMAP hydrological connectivity index from a DEM alone, using
uniform rainfall so the result reflects terrain only — no land cover or actual
rainfall pattern involved. Useful on its own to inspect terrain-driven
connectivity, and as a pre-computed **Connectivity** input to
[SCIMAP Flood](07-scimap-flood.md).

**Where to find it:** Processing Toolbox → SCIMAP → **Network Index**; toolbar
icon ("Run SCIMAP Network Index"); `&SCIMAP` menu.

**Screenshot:**
![Network Index Processing dialog](screenshots/06-network-index/dialog.png)
*The Processing dialog with the connectivity algorithm and Wetness-Connectivity Curve outputs visible.*

## Parameters

| Parameter | Description | Default |
|---|---|---|
| Digital Elevation Model (DEM) | The DEM to route flow over. | — |
| Stream Initiation Threshold (m²) | Upslope area at which a channel starts. | 800,000 |
| Connectivity algorithm | Network Index (flow-path trace) or Percentage Downslope Saturated Length (PDSL). | Flow-path trace |
| Colour ramp | Applied to the output. | SCIMAP default |
| WhiteboxTools executable | Advanced; override the auto-detected binary. | Auto-detected |

## Outputs

| Output | Description |
|---|---|
| Network Index | The connectivity raster. |
| Wetness-Connectivity Curve (PNG) (optional) | A scatter plot of each cell's topographic wetness index against its connectivity score. |
| Wetness-Connectivity Curve dataset (CSV) (optional) | The data behind that plot. |

## Notes

- Because rainfall is held uniform, this isolates the terrain contribution to
  connectivity — useful for comparing catchments, or as a stable input that
  doesn't need to be recomputed for every rainfall scenario in SCIMAP Flood.
- The Wetness-Connectivity Curve outputs need matplotlib to be available in the
  QGIS Python environment; if it isn't, the raster is still produced and a
  warning explains why the plot/CSV were skipped.

**Screenshot:**
![Example Wetness-Connectivity Curve plot](screenshots/06-network-index/wetness-connectivity-curve.png)
*An example Wetness-Connectivity Curve PNG output.*

Next: [SCIMAP Flood](07-scimap-flood.md).
