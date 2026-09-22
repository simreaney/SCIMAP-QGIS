# SCIMAP Flood

Identifies potential source areas of flood peak water, to help spatially
target nature-based solutions that slow or store water. Implements the
algorithm from Reaney, S.M. (2022) "Spatial targeting of nature-based
solutions for flood risk management within river catchments", *Journal of
Flood Risk Management*, e12803.

Unlike SCIMAP Sediment/FIO, this tool takes **pre-computed** input rasters
rather than a DEM and land cover — connectivity (e.g. from
[Network Index](06-network-index.md)), a runoff/weights raster, one or more
rainfall pattern rasters, and one or more overland flow distance rasters (e.g.
from [Overland Flow Distance to Point](08-overland-flow-distance-to-point.md)).
It combines every rainfall × overland-flow-distance combination and reports
the per-pixel mean and standard deviation across them.

**Where to find it:** Processing Toolbox → SCIMAP → **SCIMAP Flood**; toolbar
icon ("Run SCIMAP Flood"); `&SCIMAP` menu; or the
[SCIMAP Panel](01-scimap-panel.md)'s Flood tab, which also offers click-on-map
impact points and a shortcut to compute the overland flow distance rasters.

**Screenshot:**
![SCIMAP Flood Processing dialog](screenshots/07-scimap-flood/dialog.png)
*The Processing dialog with rainfall and overland-flow-distance raster lists populated.*

## Parameters

| Parameter | Description |
|---|---|
| Connectivity Raster (pre-computed) | A connectivity raster, e.g. from Network Index. |
| Runoff / Land Cover Weights Raster (pre-computed) | A runoff-generation-potential raster. |
| Rainfall Pattern Rasters | One or more rainfall pattern rasters — every one supplied is used. |
| Overland Flow Distance Rasters (pre-computed) | One or more overland flow distance rasters. |
| Colour ramp | Applied to both output rasters. |

## Outputs

| Output | Description |
|---|---|
| SCIMAP-Flood Mean | Per-pixel mean response across every rainfall × overland-flow-distance combination, normalised by its own global maximum. |
| SCIMAP-Flood Standard Deviation | Per-pixel standard deviation across the same combinations — high values mark cells whose flood response depends strongly on which rainfall pattern or impact point is considered. |

## How it works

For each cell:

```
base = connectivity × runoff
rainfall_norm   = (rainfall − raster_min) / (raster_max − raster_min)
overland_norm   = peak-at-median stretch to [0, 1] (1.0 at the median overland
                   flow distance, falling to 0.0 at the min/max)

for every (rainfall, overland-flow-distance) combination:
    product = base × rainfall_norm × overland_norm

mean  = per-pixel mean(product) over valid combinations at that pixel
stdev = per-pixel population standard deviation of the same set
```

The mean output is finally divided by its own global maximum. Every input
raster's own declared nodata value, and any negative value, is treated as
invalid.

## Notes

- All four rasters must be aligned to a common grid — this tool does not
  resample them.
- Runs window-by-window rather than loading whole rasters into memory, so peak
  memory use does not scale with raster size.
- The mean/standard-deviation pair is best read together: high mean with low
  standard deviation is a dependable flood-response hotspot; high mean with
  high standard deviation depends heavily on the specific rainfall pattern and
  impact point chosen.

Next: [Overland Flow Distance to Point](08-overland-flow-distance-to-point.md).
