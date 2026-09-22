# Overland Flow Distance to Point

Computes the directional downslope flow-path distance across the land surface
from every cell to the nearest of one or more target points, using
WhiteboxTools' `DownslopeDistanceToStream` over a rasterised point set.

Unlike a straight-line or cost-distance surface, this follows the D8 downslope
flow direction derived from the DEM: water only travels downhill, so cells that
do not drain to a target point receive no distance (NoData) rather than an
omnidirectional distance that would imply they could reach it.

**Where to find it:** Processing Toolbox → SCIMAP → **Overland Flow Distance to
Point**; toolbar icon; `&SCIMAP` menu; or the
[SCIMAP Panel](01-scimap-panel.md)'s Flood tab, via the **Calc. OFD** button
after picking impact points on the map.

**Screenshot:**
![Overland Flow Distance to Point Processing dialog](screenshots/08-overland-flow-distance-to-point/dialog.png)
*The Processing dialog.*

## Parameters

| Parameter | Description | Default |
|---|---|---|
| Digital Elevation Model (DEM) | The DEM to route flow over. | — |
| Target Point(s) | A point vector layer — one or more targets. | — |
| Channel Network (optional) | A line layer; when supplied, each target point is snapped onto the nearest channel first. | Optional |
| Maximum snap distance to channel (map units) | Points further than this from any channel are left unsnapped. Ignored with no channel network. | 100 |
| Snap target point(s) to the channel network by flow accumulation | An alternative snap mode, as used in [Delineate Catchment](02-delineate-catchment.md), based on nearby flow accumulation rather than the supplied channel layer. | Off |
| Snap search radius (map units) | Ignored unless the accumulation-based snap above is enabled. | 250 |
| Minimum contributing area to snap onto (m²) | Ignored unless the accumulation-based snap above is enabled. | 800,000 |
| Minimum accumulation ratio vs the clicked cell | Ignored unless the accumulation-based snap above is enabled. | 100 |
| Breach DEM depressions before computing flow direction | Recommended — avoids broken flow paths at sinks. | On |
| Colour ramp | Applied to the output. | SCIMAP default |
| WhiteboxTools executable | Advanced; override the auto-detected binary. | Auto-detected |

## Outputs

| Output | Description |
|---|---|
| Overland Flow Travel Distance | Downslope distance (map units) from each cell to its nearest target point; NoData where a cell's flow path never reaches one. |

## Notes

- Two independent snapping options are available and can both be used: **snap
  to a supplied channel network** (nearest line, within a maximum distance),
  and/or **snap by flow accumulation** (the same logic Delineate Catchment
  uses). Neither is required — points can also be used exactly as supplied.
- This tool's output is one of SCIMAP Flood's required inputs; the
  [SCIMAP Panel](01-scimap-panel.md)'s Flood tab runs it directly from picked
  impact points via **Calc. OFD**.

Next: [Export SCIMAP Results](09-export-scimap-results.md).
