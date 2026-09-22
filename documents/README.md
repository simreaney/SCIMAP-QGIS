# SCIMAP Toolkit for QGIS — User Guide

This guide describes every part of the **SCIMAP Toolkit** QGIS plugin: the guided
panel and each of its twelve Processing tools. It is written for people using the
plugin, not for developers — for the code, see [qgis_plugin/](../../qgis_plugin/).

Each page has one or more screenshot placeholders, written as ordinary Markdown
images pointing into a `screenshots/` folder next to this guide. Save a PNG at
the path shown under each placeholder and it will appear automatically — no
further editing needed. A suggested shot list is given as a caption under each
placeholder.

## Start Here

- [Overview and Installation](00-overview.md)
  What the plugin does, requirements (QGIS 3.28–4.99, WhiteboxTools), how to
  install it, and where its tools appear (toolbar, `&SCIMAP` menu, Processing
  Toolbox).

- [The SCIMAP Panel](01-scimap-panel.md)
  The guided dock panel that mirrors the SCIMAP web application: click-on-map
  points, an editable risk weight table, and the Catchment / Parameters / Run /
  Flood / Results tabs.

## Core Workflow Tools

Run roughly in this order for a first SCIMAP Sediment or SCIMAP FIO map:

1. [Delineate Catchment](02-delineate-catchment.md) — outline the area upstream of a pour point.
2. [Apply Land Cover Risk Weights](03-apply-land-cover-risk-weights.md) — turn a land cover raster into a risk-weighted raster on its own.
3. [SCIMAP Sediment](04-scimap-sediment.md) — diffuse pollution / fine sediment risk from a DEM, land cover and rainfall.
4. [SCIMAP FIO](05-scimap-fio.md) — seasonal faecal indicator organism delivery risk.
5. [Network Index](06-network-index.md) — terrain-only hydrological connectivity, and an input to SCIMAP Flood.
6. [SCIMAP Flood](07-scimap-flood.md) — spatial targeting of nature-based flood solutions.
7. [Overland Flow Distance to Point](08-overland-flow-distance-to-point.md) — downslope travel distance to one or more target points.
8. [Export SCIMAP Results](09-export-scimap-results.md) — GeoPackage, KML, point vectors and Cloud-Optimised GeoTIFF.
9. [Create Web Dashboard](10-create-web-dashboard.md) — a self-contained, offline, shareable results folder.

## SCIMAP Fitted (run in order)

Infers land cover risk weights from observed water quality instead of assuming
them. The three tools are numbered because each needs the previous one's output.

1. [SCIMAP Fitted 1: Catchment Statistics](11-fitted-1-catchment-statistics.md)
2. [SCIMAP Fitted 2: Calibrate Weights](12-fitted-2-calibrate-weights.md)
3. [SCIMAP Fitted 3: Ensemble Risk Maps](13-fitted-3-ensemble-risk-maps.md)

## Notes

- All twelve tools also appear as ordinary algorithms in the **Processing
  Toolbox**, under the **SCIMAP** provider, so they can be chained together in
  Processing models and batch-run over multiple catchments.
- Every tool depends on the WhiteboxTools executable also being installed — see
  [Overview and Installation](00-overview.md).
