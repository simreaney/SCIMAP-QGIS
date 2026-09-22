# Delineate Catchment

Delineates the catchment (drainage area) upstream of a pour point — the outlet
you're interested in, such as a monitoring station or a point of concern.

Clicking beside a channel rather than exactly on it is the normal case, so the
clicked point is snapped onto the nearest cell whose upslope contributing area
exceeds a threshold before the watershed is delineated. This means a click that
is close to, but not precisely on, the mapped channel still returns the
catchment you meant.

**Where to find it:** Processing Toolbox → SCIMAP → Catchment → **Delineate
Catchment**; toolbar icon; `&SCIMAP` menu → **Delineate Catchment**; or the
[SCIMAP Panel](01-scimap-panel.md)'s Catchment tab for a simplified version of
the same tool.

**Screenshot:**
![Delineate Catchment Processing dialog](screenshots/02-delineate-catchment/dialog.png)
*The Processing dialog, ideally with a pour point already picked on the map.*

## Parameters

| Parameter | Description | Default |
|---|---|---|
| Digital Elevation Model (DEM) | The DEM raster to route flow over. | — |
| Pour point (catchment outlet) | The outlet point. Use the map button to pick it from the canvas. | — |
| Snap search radius (map units) | Radius searched for a channel cell to snap the pour point onto. | 250 |
| Minimum contributing area to snap onto (m²) | Upslope area a cell must have before the snap will land on it. | 800,000 |
| Minimum accumulation ratio vs the clicked cell | How much larger a nearby cell's accumulation must be, relative to the clicked cell, to be preferred as the snap target. | 100 |
| Drop catchment parts smaller than (m²) | Discards small disconnected polygon fragments below this area. | 0 (keep all) |
| Breach depressions before routing | Fills DEM sinks before computing flow direction. | On |
| WhiteboxTools executable | Advanced parameter; override the auto-detected `whitebox_tools` binary. | Auto-detected |

## Outputs

| Output | Description |
|---|---|
| Catchment Boundary | The delineated catchment as a polygon layer. |
| Snapped Pour Point (optional) | The pour point after snapping, with `accum_cel` (upslope cells), `area_m2` and `moved_m` (how far it moved) attributes. |
| Basin Raster (optional) | The raw watershed raster before vectorisation. |

## How it works

1. Breach DEM depressions (`BreachDepressions`), if enabled.
2. Compute the D8 flow direction pointer and D8 flow accumulation.
3. Snap the pour point onto the nearest cell exceeding the contributing-area
   threshold, within the search radius, preferring cells with substantially
   higher accumulation than the clicked cell.
4. Delineate the watershed (`Watershed`) from the snapped pour point.
5. Vectorise the basin raster into the catchment boundary polygon.

The log reports how far the point moved when snapping and the resulting
catchment area, so it's easy to check the snap landed on the intended channel
rather than a neighbouring one.

Next: [Apply Land Cover Risk Weights](03-apply-land-cover-risk-weights.md).
