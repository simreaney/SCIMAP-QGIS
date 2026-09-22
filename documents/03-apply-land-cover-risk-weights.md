# Apply Land Cover Risk Weights

Reclassifies a land cover raster into SCIMAP risk weights as a standalone
raster. [SCIMAP Sediment](04-scimap-sediment.md) performs this same
reclassification internally, but this tool exposes it on its own so a weighted
raster can be built once, inspected, and reused across several runs or chained
into a Processing model.

Raw land cover class IDs are first mapped onto SCIMAP's seven classes using the
remap table (pre-filled with the UK Centre for Ecology & Hydrology Land Cover
Map mapping), and each SCIMAP class is then given its risk weight. Land cover
values present in the data but absent from the weight table fall back to a
nominated class.

**Where to find it:** Processing Toolbox → SCIMAP → Preparation → **Apply Land
Cover Risk Weights**; toolbar icon; `&SCIMAP` menu → **Apply Land Cover Risk
Weights**.

**Screenshot:**
![Apply Land Cover Risk Weights Processing dialog](screenshots/03-apply-land-cover-risk-weights/dialog.png)
*The Processing dialog, showing the remap and weight matrix tables.*

## Parameters

| Parameter | Description | Default |
|---|---|---|
| Land Cover Map | The land cover raster to reclassify. | — |
| Land cover already uses SCIMAP classes (1-7) | Tick if the raster is already coded 1–7 rather than raw land cover IDs. | Off |
| Land cover class → SCIMAP class | Matrix mapping each land cover ID onto a SCIMAP class. Pre-filled with the CEH Land Cover Map mapping. | CEH mapping |
| SCIMAP class → risk weight | Matrix of risk weight (0–1000) per SCIMAP class. | SCIMAP defaults |
| Parameter set XML | An XML file (overrides the weight table above) — the same format the [SCIMAP Panel](01-scimap-panel.md)'s Parameters tab imports/exports, interchangeable with the SCIMAP web application. | Optional |
| SCIMAP class used for unmapped land cover values | Fallback class for values not present in the remap table. | 6 (Other) |
| Colour ramp | The colour ramp applied to the output. | SCIMAP default |

## Outputs

| Output | Description |
|---|---|
| Land Cover Risk Weighting | Continuous raster of risk weight per cell. |
| SCIMAP Land Cover Classes (optional) | Integer raster of SCIMAP class (1–7) per cell, useful for checking the remap visually. |

## Notes

- A land cover classification scheme changed at LCM2015 (UKCEH changed its
  class list), so if reading a 2015-or-later raster with the older 23-class
  table, Urban and Suburban can silently map to "Other" — check the remap table
  matches the vintage of the land cover raster in use.
- The output of this tool can be fed straight into SCIMAP Sediment's **Land
  Cover Map / Risk Weighting** input with **Land cover is already a risk
  weighting** ticked, to skip reclassifying twice.

Next: [SCIMAP Sediment](04-scimap-sediment.md).
