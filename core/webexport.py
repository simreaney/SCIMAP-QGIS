"""Turn SCIMAP result rasters and vectors into browser-ready payloads.

The dashboard this feeds is opened straight off disk with ``file://``, which
rules out most of the ways a web page normally gets data:

* ``fetch``/``XMLHttpRequest`` are blocked, so every array ships inside a
  classic ``<script>`` that assigns to a global — never a ``.json`` file.
* WebGL refuses to upload a texture built from a ``file://`` image, because the
  document has an opaque origin. Terrain and drape textures are therefore built
  in JavaScript from raw arrays; they are never loaded as images.
* Map *tiles*, by contrast, are plain ``<img>`` elements and work fine, which is
  why the 2D view gets a real XYZ pyramid.

Everything here is bounded-memory: sources are read through a size-capped
``gdal.Warp`` into an in-memory dataset rather than ``ReadAsArray()`` on the
full raster, and base64 is streamed to disk in chunks.

Imports GDAL and NumPy but deliberately *not* QGIS, so it can be exercised
outside a QGIS session.
"""

import base64
import json
import math
import os
import struct
import zlib

import numpy as np
from osgeo import gdal, ogr, osr

from . import ramps
from . import stats

# Binary encoding lives in core/encoding.py, which has no GDAL dependency so it
# can be tested anywhere. Re-exported here because callers work with one module.
from .encoding import (  # noqa: F401
    NODATA_I16,
    NODATA_U8,
    QUANT_MAX,
    _Blob,
    _deflate_raw,
    array_payload,
    dequantise,
    elevation_payload,
    encode_payload,
    encode_png_indexed,
    encode_png_rgba,
    png_data_uri,
    quantise,
    write_js_global,
    write_png_indexed,
    write_png_rgba,
    TRANSPARENT_PIXEL_URI,
)

# ── Web Mercator grid ──────────────────────────────────────────────────────

MERCATOR_RADIUS = 20037508.342789244   # half the EPSG:3857 extent, in metres
TILE_SIZE = 256

# Nodata written when warping, matching core/raster_io.NODATA.
NODATA_FLOAT = -9999.0


def _mercator_resolution(zoom):
    """Ground units per pixel at *zoom* (EPSG:3857 metres, not real metres)."""
    return (2.0 * MERCATOR_RADIUS) / (TILE_SIZE * (2 ** zoom))


def _mercator_srs():
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


class CommonGrid:
    """One EPSG:3857 raster grid that every dashboard layer is warped onto.

    Sharing a single grid is what lets the 3D drape, the 2D value probe and the
    charts all index the same array position and mean the same place on the
    ground.
    """

    __slots__ = ('width', 'height', 'bounds', 'pixel_size', 'wkt',
                 'centre_lat', 'mercator_scale', 'bounds_wgs84', 'source_crs')

    def __init__(self, width, height, bounds, wkt, bounds_wgs84, source_crs=''):
        self.width = int(width)
        self.height = int(height)
        self.bounds = tuple(float(v) for v in bounds)     # (minx, miny, maxx, maxy)
        self.wkt = wkt
        self.bounds_wgs84 = tuple(float(v) for v in bounds_wgs84)  # (w, s, e, n)
        self.source_crs = source_crs
        self.pixel_size = (
            (self.bounds[2] - self.bounds[0]) / self.width,
            (self.bounds[3] - self.bounds[1]) / self.height,
        )
        self.centre_lat = 0.5 * (self.bounds_wgs84[1] + self.bounds_wgs84[3])
        # EPSG:3857 inflates distances by 1/cos(latitude). Multiplying by this
        # turns mercator metres back into ground metres, which the 3D scene
        # needs or its relief is silently flattened (~0.58x at 54degN).
        self.mercator_scale = math.cos(math.radians(self.centre_lat))

    @property
    def geotransform(self):
        return (self.bounds[0], self.pixel_size[0], 0.0,
                self.bounds[3], 0.0, -self.pixel_size[1])

    def latitude_span(self):
        return abs(self.bounds_wgs84[3] - self.bounds_wgs84[1])

    def to_dict(self):
        return {
            'epsg': 3857,
            'width': self.width,
            'height': self.height,
            'bounds3857': list(self.bounds),
            # Leaflet wants [[south, west], [north, east]].
            'boundsWgs84': [[self.bounds_wgs84[1], self.bounds_wgs84[0]],
                            [self.bounds_wgs84[3], self.bounds_wgs84[2]]],
            'pixelSize3857': list(self.pixel_size),
            'centreLat': self.centre_lat,
            'mercatorScale': self.mercator_scale,
            'sourceCrs': self.source_crs,
        }


def _warped_bounds_3857(path):
    """Bounds of *path* in EPSG:3857, via a warped VRT so edges stay honest."""
    dataset = gdal.Open(path, gdal.GA_ReadOnly)
    if dataset is None:
        raise RuntimeError(f"Could not open raster: {path}")
    if not dataset.GetProjection():
        raise RuntimeError(
            f"{os.path.basename(path)} has no CRS, so it cannot be placed on a "
            "web map. Assign its coordinate system in QGIS and run again."
        )

    vrt = gdal.AutoCreateWarpedVRT(dataset, None, _mercator_srs().ExportToWkt())
    if vrt is None:
        raise RuntimeError(f"Could not reproject {os.path.basename(path)} to Web Mercator.")
    gt = vrt.GetGeoTransform()
    bounds = (gt[0], gt[3] + gt[5] * vrt.RasterYSize,
              gt[0] + gt[1] * vrt.RasterXSize, gt[3])
    source_crs = _crs_label(dataset)
    vrt = None
    dataset = None
    return bounds, source_crs


def _crs_label(dataset):
    srs = osr.SpatialReference(wkt=dataset.GetProjection())
    code = srs.GetAuthorityCode(None)
    name = srs.GetName() or 'unknown'
    return f"EPSG:{code} ({name})" if code else name


def build_common_grid(source_paths, max_px=1024, pad_fraction=0.02):
    """Build the shared Web Mercator grid covering every source raster."""
    paths = [p for p in source_paths if p]
    if not paths:
        raise RuntimeError("At least one raster is needed to build a dashboard.")

    minx = miny = float('inf')
    maxx = maxy = float('-inf')
    source_crs = ''
    for path in paths:
        bounds, crs = _warped_bounds_3857(path)
        source_crs = source_crs or crs
        minx, miny = min(minx, bounds[0]), min(miny, bounds[1])
        maxx, maxy = max(maxx, bounds[2]), max(maxy, bounds[3])

    width_m, height_m = maxx - minx, maxy - miny
    if width_m <= 0 or height_m <= 0:
        raise RuntimeError("The input rasters have an empty extent.")

    pad = pad_fraction * max(width_m, height_m)
    minx, miny, maxx, maxy = minx - pad, miny - pad, maxx + pad, maxy + pad
    width_m, height_m = maxx - minx, maxy - miny

    max_px = max(int(max_px), 64)
    if width_m >= height_m:
        width = max_px
        height = max(int(round(max_px * height_m / width_m)), 1)
    else:
        height = max_px
        width = max(int(round(max_px * width_m / height_m)), 1)

    return CommonGrid(width, height, (minx, miny, maxx, maxy),
                      _mercator_srs().ExportToWkt(),
                      _bounds_to_wgs84((minx, miny, maxx, maxy)), source_crs)


def _bounds_to_wgs84(bounds):
    src = _mercator_srs()
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(4326)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(src, dst)
    west, south, _ = transform.TransformPoint(bounds[0], bounds[1])
    east, north, _ = transform.TransformPoint(bounds[2], bounds[3])
    return (west, south, east, north)


def warp_to_grid(source_path, grid, resample='bilinear', dest_path=''):
    """Warp *source_path* onto *grid*, in memory unless *dest_path* is given."""
    return gdal.Warp(
        dest_path or '', source_path,
        format='GTiff' if dest_path else 'MEM',
        outputBounds=grid.bounds,
        width=grid.width, height=grid.height,
        dstSRS=grid.wkt, dstNodata=NODATA_FLOAT,
        resampleAlg=resample,
        outputType=gdal.GDT_Float32,
        multithread=True,
        creationOptions=(['TILED=YES', 'COMPRESS=LZW', 'BIGTIFF=IF_SAFER'] if dest_path else []),
    )



def read_grid_array(dataset, band=1):
    """Read a warped dataset as float32 with NaN in place of nodata."""
    raster_band = dataset.GetRasterBand(band)
    array = raster_band.ReadAsArray().astype(np.float32)
    nodata = raster_band.GetNoDataValue()
    if nodata is not None:
        array[np.isclose(array, nodata, rtol=0.0, atol=1e-6)] = np.nan
    array[~np.isfinite(array)] = np.nan
    return array


# ── Statistics ─────────────────────────────────────────────────────────────

def cumulative_cut(source_path, band=1, low=5.0, high=95.0):
    """The band's *low*/*high* percentile values, from the full-resolution source.

    Reproduces what ``QgsRasterDataProvider.cumulativeCut`` gives
    ``core/styling.percentile_range``, so the dashboard's colour stretch matches
    what the user sees on the QGIS canvas. Uses GDAL's own out-of-core
    histogram rather than reading the raster into NumPy.
    """
    dataset = gdal.Open(source_path, gdal.GA_ReadOnly)
    if dataset is None:
        raise RuntimeError(f"Could not open raster: {source_path}")
    raster_band = dataset.GetRasterBand(band)

    vmin, vmax = raster_band.ComputeRasterMinMax(True)
    if not (math.isfinite(vmin) and math.isfinite(vmax)) or vmax <= vmin:
        dataset = None
        return (float(vmin), float(vmin) + 1.0)

    buckets = 4096
    counts = raster_band.GetHistogram(vmin, vmax, buckets, False, False)
    dataset = None

    total = float(sum(counts))
    if total <= 0:
        return (float(vmin), float(vmax))

    width = (vmax - vmin) / buckets
    lower = _histogram_quantile(counts, total, low / 100.0, vmin, width)
    upper = _histogram_quantile(counts, total, high / 100.0, vmin, width)
    if upper <= lower:
        return (float(vmin), float(vmax))
    return (float(lower), float(upper))


def _histogram_quantile(counts, total, fraction, vmin, width):
    target = total * fraction
    running = 0.0
    for index, count in enumerate(counts):
        running += count
        if running >= target:
            return vmin + index * width
    return vmin + len(counts) * width


def raster_stats(source_path, band=1):
    """Min/max/mean/stddev and valid-cell count, from the full-resolution source."""
    dataset = gdal.Open(source_path, gdal.GA_ReadOnly)
    if dataset is None:
        raise RuntimeError(f"Could not open raster: {source_path}")
    raster_band = dataset.GetRasterBand(band)
    try:
        vmin, vmax, mean, stddev = raster_band.ComputeStatistics(True)
    except RuntimeError:
        dataset = None
        return {}
    stats = {
        'min': float(vmin), 'max': float(vmax),
        'mean': float(mean), 'std': float(stddev),
        'width': int(dataset.RasterXSize), 'height': int(dataset.RasterYSize),
    }
    dataset = None
    return stats


def raster_histogram(source_path, band=1, bins=48, vmin=None, vmax=None):
    """A histogram of the full-resolution source, for the dashboard charts."""
    dataset = gdal.Open(source_path, gdal.GA_ReadOnly)
    if dataset is None:
        raise RuntimeError(f"Could not open raster: {source_path}")
    raster_band = dataset.GetRasterBand(band)

    if vmin is None or vmax is None:
        vmin, vmax = raster_band.ComputeRasterMinMax(True)
    vmin, vmax = float(vmin), float(vmax)
    if vmax <= vmin:
        vmax = vmin + 1.0

    counts = raster_band.GetHistogram(vmin, vmax, int(bins), False, False)
    dataset = None
    edges = [vmin + (vmax - vmin) * i / float(bins) for i in range(int(bins) + 1)]
    return {'edges': edges, 'counts': [int(c) for c in counts]}


def risk_concentration(source_path, band=1, buckets=16384):
    """How unevenly risk is spread across the landscape, cell by cell.

    A Lorenz curve over *every* valid cell, not just the channel network, so a
    catchment can be asked how much of its land produces how much of its risk.
    The arithmetic lives in :func:`core.stats.lorenz_from_histogram`; this reads
    the raster for it.

    Built from GDAL's own histogram rather than a NumPy sort, so a
    200-million-cell raster costs two out-of-core passes and a few kilobytes
    rather than gigabytes of RAM. Measured against a full exact sort of the
    sample catchment, every published figure lands within 0.05 percentage
    points and the Gini coefficient within 0.001.
    """
    dataset = gdal.Open(source_path, gdal.GA_ReadOnly)
    if dataset is None:
        raise RuntimeError(f"Could not open raster: {source_path}")
    raster_band = dataset.GetRasterBand(band)

    # Exact, not approximate: an approximate maximum drops every cell above it
    # from the histogram, and those are precisely the cells that matter here.
    vmin, vmax = raster_band.ComputeRasterMinMax(False)
    if not (math.isfinite(vmin) and math.isfinite(vmax)) or vmax <= vmin:
        dataset = None
        return None

    buckets = int(buckets)
    # Pad by half a bucket at each end. GDAL drops values sitting exactly on
    # the lower bound, and on a risk surface that bound is 0.0 — not a rare
    # edge case but every cell of zero-risk land, 5% of the catchment on the
    # sample data. Losing them silently inflates every share-of-land figure.
    # Same reason byte histograms are asked for over (-0.5, 255.5), not (0, 255).
    width = (vmax - vmin) / buckets
    edge, top = vmin - width / 2.0, vmax + width / 2.0
    counts = raster_band.GetHistogram(edge, top, buckets, False, False)
    transform = dataset.GetGeoTransform()
    dataset = None

    curve = stats.lorenz_from_histogram(counts, edge, (top - edge) / buckets)
    if curve is None:
        return None

    cell_area = abs(transform[1] * transform[5]) if transform else 0.0
    curve['areaKm2'] = round(curve['cells'] * cell_area / 1e6, 2) if cell_area else None
    return curve


def hillshade_source(dem_path, out_path, z_factor=1.0, azimuth=315.0, altitude=45.0):
    """Render a hillshade from the DEM **in the DEM's own CRS**.

    Shading before reprojection is deliberate: a projected DEM's horizontal
    units no longer match its vertical units (EPSG:3857 metres are inflated by
    1/cos(lat)), so hillshading a warped DEM would need a latitude-dependent
    z-factor to avoid flattening the relief. Doing it in the source's metric CRS
    sidesteps that entirely.
    """
    gdal.DEMProcessing(
        out_path, dem_path, 'hillshade',
        format='GTiff', zFactor=z_factor, azimuth=azimuth, altitude=altitude,
        computeEdges=True,
        creationOptions=['TILED=YES', 'COMPRESS=LZW', 'BIGTIFF=IF_SAFER'],
    )
    if not os.path.exists(out_path):
        raise RuntimeError("Could not render a hillshade from the DEM.")
    return out_path


# ── XYZ tile pyramid ───────────────────────────────────────────────────────

def _tile_x(x_metres, resolution):
    return int(math.floor((x_metres + MERCATOR_RADIUS) / (TILE_SIZE * resolution)))


def _tile_y(y_metres, resolution):
    return int(math.floor((MERCATOR_RADIUS - y_metres) / (TILE_SIZE * resolution)))


def tile_range(bounds, zoom):
    """Inclusive ``(x0, y0, x1, y1)`` XYZ tile range covering *bounds*."""
    resolution = _mercator_resolution(zoom)
    limit = (2 ** zoom) - 1
    x0 = max(0, min(limit, _tile_x(bounds[0], resolution)))
    x1 = max(0, min(limit, _tile_x(bounds[2] - 1e-9, resolution)))
    y0 = max(0, min(limit, _tile_y(bounds[3] - 1e-9, resolution)))
    y1 = max(0, min(limit, _tile_y(bounds[1], resolution)))
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def native_zoom(source_path, bounds=None):
    """The zoom level whose pixels are closest to the source's own resolution.

    Tiling beyond this invents detail the analysis never had, so it is the
    natural cap for ``max_zoom``.
    """
    dataset = gdal.Open(source_path, gdal.GA_ReadOnly)
    if dataset is None:
        raise RuntimeError(f"Could not open raster: {source_path}")
    vrt = gdal.AutoCreateWarpedVRT(dataset, None, _mercator_srs().ExportToWkt())
    resolution = abs(vrt.GetGeoTransform()[1]) if vrt is not None else 0.0
    vrt = None
    dataset = None
    if resolution <= 0:
        return 14
    zoom = math.log2((2.0 * MERCATOR_RADIUS) / (TILE_SIZE * resolution))
    return int(max(0, min(22, round(zoom))))


def zoom_range(source_path, grid, max_zoom=None, hard_cap=19):
    """Pick ``(min_zoom, max_zoom)``: native resolution up top, whole extent below.

    The top level is the source's own resolution, not one beyond it. Tiling a
    level further would quadruple the pyramid (on a typical 5 m catchment: 418
    tiles / 15 MB instead of 133 tiles / 4 MB) purely to store an upscale of
    pixels the analysis never resolved. Leaflet is told ``maxNativeZoom`` and
    stretches the top level itself, so the user can still zoom in freely.
    """
    top = native_zoom(source_path) if max_zoom is None else int(max_zoom)
    top = int(max(0, min(hard_cap, top)))

    bottom = top
    while bottom > 0:
        x0, y0, x1, y1 = tile_range(grid.bounds, bottom)
        if (x1 - x0 + 1) <= 2 and (y1 - y0 + 1) <= 2:
            break
        bottom -= 1
    return (bottom, top)


def count_tiles(grid, min_zoom, max_zoom):
    """Upper bound on the tiles a pyramid will hold, before any are skipped."""
    total = 0
    for zoom in range(min_zoom, max_zoom + 1):
        x0, y0, x1, y1 = tile_range(grid.bounds, zoom)
        total += (x1 - x0 + 1) * (y1 - y0 + 1)
    return total


def build_tile_pyramid(source_path, out_dir, ramp_name, vmin, vmax, grid,
                       min_zoom, max_zoom, feedback=None, progress=None,
                       resample='bilinear'):
    """Write an XYZ pyramid of colour-ramped PNG tiles under *out_dir*.

    Tiles are plain ``<img>`` elements to Leaflet, so unlike WebGL textures they
    load happily from ``file://`` — which is why the 2D map gets a real pyramid
    rather than one stretched overlay.

    Each zoom level is warped one **tile row** at a time: a single warp per row
    keeps GDAL calls down without ever holding a whole level in memory (a top
    level can be tens of thousands of pixels across). Fully transparent tiles
    are never written; Leaflet renders the gaps with a transparent errorTileUrl.
    """
    lut = ramps.ramp_lut(ramp_name)
    written = 0
    skipped = 0
    levels = list(range(int(min_zoom), int(max_zoom) + 1))
    total_rows = 0
    for zoom in levels:
        x0, y0, x1, y1 = tile_range(grid.bounds, zoom)
        total_rows += (y1 - y0 + 1)
    done_rows = 0

    for zoom in levels:
        resolution = _mercator_resolution(zoom)
        x0, y0, x1, y1 = tile_range(grid.bounds, zoom)
        columns = x1 - x0 + 1
        row_width = columns * TILE_SIZE

        west = -MERCATOR_RADIUS + x0 * TILE_SIZE * resolution
        east = west + row_width * resolution

        for tile_y in range(y0, y1 + 1):
            if feedback is not None and feedback.isCanceled():
                return {'tiles': written, 'skipped': skipped,
                        'minzoom': min_zoom, 'maxzoom': max_zoom, 'canceled': True}

            north = MERCATOR_RADIUS - tile_y * TILE_SIZE * resolution
            south = north - TILE_SIZE * resolution

            strip = gdal.Warp(
                '', source_path, format='MEM',
                outputBounds=(west, south, east, north),
                width=row_width, height=TILE_SIZE,
                dstSRS=grid.wkt, dstNodata=NODATA_FLOAT,
                resampleAlg=resample, outputType=gdal.GDT_Float32,
                multithread=True,
            )
            if strip is None:
                continue
            values = read_grid_array(strip)
            strip = None

            codes = quantise(values, vmin, vmax)
            valid = codes != NODATA_U8
            del values

            for index in range(columns):
                column = slice(index * TILE_SIZE, (index + 1) * TILE_SIZE)
                tile_valid = valid[:, column]
                if not tile_valid.any():
                    skipped += 1
                    continue

                tile_dir = os.path.join(out_dir, str(zoom), str(x0 + index))
                os.makedirs(tile_dir, exist_ok=True)
                write_png_indexed(os.path.join(tile_dir, f"{tile_y}.png"),
                                  codes[:, column], lut)
                written += 1

            del codes, valid
            done_rows += 1
            if progress is not None and total_rows:
                progress(done_rows / float(total_rows))

    return {'tiles': written, 'skipped': skipped,
            'minzoom': int(min_zoom), 'maxzoom': int(max_zoom), 'canceled': False}


# How far past the last real tile level Leaflet may stretch the top tiles.
# Leaflet handles this itself via maxNativeZoom; it costs nothing on disk.
OVERZOOM_LEVELS = 4


# ── Vectors and tabular data ───────────────────────────────────────────────

def _wgs84_transform(layer, source_wkt=None):
    """Build a transform from *layer*'s CRS to WGS-84.

    Same approach as ``core/vectors.export_vector_kml``: take the CRS from the
    layer unless the caller knows better, and force traditional axis order so
    coordinates come back as lon/lat rather than lat/lon.
    """
    source = osr.SpatialReference()
    if source_wkt:
        source.ImportFromWkt(source_wkt)
    else:
        layer_srs = layer.GetSpatialRef()
        if layer_srs is None:
            raise RuntimeError(
                "The vector layer has no CRS, so it cannot be placed on a web "
                "map. Assign its coordinate system in QGIS and run again."
            )
        source = layer_srs.Clone()
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    target = osr.SpatialReference()
    target.ImportFromEPSG(4326)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return osr.CoordinateTransformation(source, target)


def _round_coords(node, places):
    """Round a GeoJSON coordinate tree in place-ish, returning the rounded tree.

    Six decimal places is about 0.1 m at these latitudes — far finer than any
    SCIMAP output — and typically halves the size of the embedded geometry.
    """
    if isinstance(node, (list, tuple)):
        if node and isinstance(node[0], (int, float)):
            return [round(float(value), places) for value in node]
        return [_round_coords(child, places) for child in node]
    return node


def vector_to_geojson(vector_path, source_wkt=None, fields=None, precision=6,
                      max_features=None, sort_field=None, extra_props=None):
    """Read a vector layer into a WGS-84 GeoJSON dict ready to embed in JS.

    Returns a dict rather than writing a file because the dashboard loads its
    data from classic scripts, not ``fetch``.

    When *max_features* would be exceeded the layer is trimmed to the highest
    *sort_field* values, so capping a risk layer keeps the risky features rather
    than an arbitrary slice.
    """
    dataset = ogr.Open(vector_path)
    if dataset is None:
        raise RuntimeError(f"Could not open vector: {vector_path}")
    layer = dataset.GetLayer(0)
    transform = _wgs84_transform(layer, source_wkt)

    definition = layer.GetLayerDefn()
    available = [definition.GetFieldDefn(i).GetNameRef()
                 for i in range(definition.GetFieldCount())]
    wanted = [name for name in (fields or available) if name in available]

    features = []
    layer.ResetReading()
    for feature in layer:
        geometry = feature.GetGeometryRef()
        if geometry is None or geometry.IsEmpty():
            continue
        geometry = geometry.Clone()
        if geometry.Transform(transform) != 0:
            continue

        properties = {}
        for name in wanted:
            index = feature.GetFieldIndex(name)
            if index >= 0 and feature.IsFieldSet(index) and not feature.IsFieldNull(index):
                properties[name] = feature.GetField(index)
        if extra_props is not None:
            properties.update(extra_props(feature, geometry) or {})

        shape = json.loads(geometry.ExportToJson())
        shape['coordinates'] = _round_coords(shape.get('coordinates', []), precision)
        features.append({'type': 'Feature', 'geometry': shape, 'properties': properties})

    dataset = None

    if max_features and len(features) > int(max_features):
        if sort_field:
            features.sort(key=lambda f: _as_float(f['properties'].get(sort_field)), reverse=True)
        features = features[:int(max_features)]

    return {'type': 'FeatureCollection', 'features': features}


def _as_float(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def read_scatter_csv(csv_path, max_points=4000, seed=0):
    """Read the wetness-connectivity CSV written by ``core/plotting.py``.

    That file already caps itself at 50,000 rows; this trims further to
    something a browser can draw smoothly, keeping a uniform random sample so
    the shape of the cloud survives.
    """
    import csv as _csv

    xs, ys = [], []
    with open(csv_path, 'r', newline='') as handle:
        reader = _csv.reader(handle)
        header = next(reader, None)
        if header and not _is_number(header[0]):
            pass                       # a real header row; already consumed
        elif header:
            xs.append(float(header[0]))
            ys.append(float(header[1]))
        for row in reader:
            if len(row) < 2:
                continue
            try:
                xs.append(float(row[0]))
                ys.append(float(row[1]))
            except ValueError:
                continue

    x = np.asarray(xs, dtype=np.float32)
    y = np.asarray(ys, dtype=np.float32)
    keep = np.isfinite(x) & np.isfinite(y)
    x, y = x[keep], y[keep]

    total = int(x.size)
    if max_points and total > int(max_points):
        rng = np.random.default_rng(seed)
        pick = rng.choice(total, size=int(max_points), replace=False)
        pick.sort()
        x, y = x[pick], y[pick]

    return {
        'points': [[round(float(a), 4), round(float(b), 4)] for a, b in zip(x, y)],
        'total': total,
        'shown': int(x.size),
    }


def _is_number(value):
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True

# ── Stream network simplification ──────────────────────────────────────────

class _NodeIndex:
    """Snaps nearby endpoints onto shared nodes, using a spatial hash.

    Plain coordinate equality is not enough. ``RasterStreamsToVector`` walks the
    D8 pointer cell to cell, so consecutive links can end and begin one cell
    apart rather than on a shared vertex — and older SCIMAP outputs, produced
    before the D8 routing fix, are worse. Snapping within a cell or two rebuilds
    a connected network; an exact-match index would leave a quarter of a million
    orphan fragments.
    """

    def __init__(self, tolerance):
        self.tolerance = float(tolerance)
        self._cell = max(self.tolerance, 1e-9)
        self._buckets = {}
        self.nodes = []

    def key(self, x, y):
        cx, cy = int(math.floor(x / self._cell)), int(math.floor(y / self._cell))
        best, best_distance = None, self.tolerance ** 2
        # Search the 3x3 neighbourhood so a point just over a bucket edge still
        # finds its neighbour.
        for ix in (cx - 1, cx, cx + 1):
            for iy in (cy - 1, cy, cy + 1):
                for index in self._buckets.get((ix, iy), ()):
                    nx, ny = self.nodes[index]
                    distance = (nx - x) ** 2 + (ny - y) ** 2
                    if distance <= best_distance:
                        best, best_distance = index, distance
        if best is not None:
            return best
        index = len(self.nodes)
        self.nodes.append((x, y))
        self._buckets.setdefault((cx, cy), []).append(index)
        return index


def merge_line_network(segments, tolerance, simplify=0.0, value_key='value'):
    """Chain line fragments into reaches that break only at confluences.

    *segments* are dicts of ``{'coords': [(x, y), ...], value_key: float}`` in a
    projected CRS, so *tolerance* and *simplify* are plain metres.

    Each reach carries the mean of *value_key* over the fragments it absorbed,
    which is what turns a quarter-million anonymous fragments into a rankable
    list of "these reaches carry the most risk".
    """
    index = _NodeIndex(tolerance)
    adjacency = {}
    prepared = []

    for segment in segments:
        coords = [tuple(point[:2]) for point in segment['coords']]
        if len(coords) < 2:
            continue
        head = index.key(*coords[0])
        tail = index.key(*coords[-1])
        if head == tail and len(coords) == 2:
            continue                      # a fragment snapped down to nothing
        slot = len(prepared)
        prepared.append({'coords': coords, 'head': head, 'tail': tail,
                         'value': segment.get(value_key), 'used': False})
        adjacency.setdefault(head, []).append(slot)
        adjacency.setdefault(tail, []).append(slot)

    def walk(start, from_node):
        segment = prepared[start]
        segment['used'] = True
        coords = list(segment['coords'])
        node = segment['tail']
        if segment['head'] != from_node:
            coords.reverse()
            node = segment['head']
        members = [start]

        while len(adjacency.get(node, ())) == 2:
            nxt = [s for s in adjacency[node] if not prepared[s]['used']]
            if not nxt:
                break
            slot = nxt[0]
            following = prepared[slot]
            following['used'] = True
            members.append(slot)
            piece = list(following['coords'])
            if following['head'] != node:
                piece.reverse()
                node = following['head']
            else:
                node = following['tail']
            coords.extend(piece[1:])
        return coords, members

    reaches = []
    # Begin at confluences and dead ends so reaches break where the hydrology
    # does, then mop up any closed loops that contain no such node.
    starts = [(slot, node) for node, slots in adjacency.items() if len(slots) != 2
              for slot in slots]
    starts.extend((slot, prepared[slot]['head']) for slot in range(len(prepared)))

    for slot, node in starts:
        if prepared[slot]['used']:
            continue
        coords, members = walk(slot, node)
        if len(coords) < 2:
            continue
        if simplify:
            coords = _simplify(coords, simplify)

        values = [prepared[s]['value'] for s in members
                  if isinstance(prepared[s]['value'], (int, float))
                  and math.isfinite(prepared[s]['value'])]
        reaches.append({
            'coords': coords,
            'length': _polyline_length(coords),
            'value': (sum(values) / len(values)) if values else None,
        })

    return reaches


def _polyline_length(coords):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(coords, coords[1:]))


def _simplify(coords, tolerance):
    """Douglas-Peucker, iterative so a long reach cannot blow the stack."""
    if len(coords) < 3:
        return coords
    keep = [False] * len(coords)
    keep[0] = keep[-1] = True
    stack = [(0, len(coords) - 1)]

    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        ax, ay = coords[start]
        bx, by = coords[end]
        dx, dy = bx - ax, by - ay
        span = math.hypot(dx, dy)

        worst, worst_index = -1.0, -1
        for i in range(start + 1, end):
            px, py = coords[i]
            if span == 0.0:
                distance = math.hypot(px - ax, py - ay)
            else:
                distance = abs(dy * px - dx * py + bx * ay - by * ax) / span
            if distance > worst:
                worst, worst_index = distance, i

        if worst > tolerance and worst_index > 0:
            keep[worst_index] = True
            stack.append((start, worst_index))
            stack.append((worst_index, end))

    return [point for point, flag in zip(coords, keep) if flag]


def _read_line_segments(vector_path, value_field=None):
    """Read line fragments in their own CRS, plus that CRS's WKT."""
    dataset = ogr.Open(vector_path)
    if dataset is None:
        raise RuntimeError(f"Could not open vector: {vector_path}")
    layer = dataset.GetLayer(0)
    layer_srs = layer.GetSpatialRef()
    source_wkt = layer_srs.ExportToWkt() if layer_srs is not None else ''

    segments = []
    layer.ResetReading()
    for feature in layer:
        geometry = feature.GetGeometryRef()
        if geometry is None or geometry.IsEmpty():
            continue
        value = None
        if value_field:
            index = feature.GetFieldIndex(value_field)
            if index >= 0 and feature.IsFieldSet(index) and not feature.IsFieldNull(index):
                value = _as_float(feature.GetField(index), None)

        for part in _iter_line_parts(geometry):
            points = part.GetPoints()
            if points and len(points) >= 2:
                segments.append({'coords': [(p[0], p[1]) for p in points], 'value': value})

    dataset = None
    return segments, source_wkt


def _iter_line_parts(geometry):
    name = geometry.GetGeometryName()
    if name == 'LINESTRING':
        yield geometry
    elif name in ('MULTILINESTRING', 'GEOMETRYCOLLECTION'):
        for i in range(geometry.GetGeometryCount()):
            for part in _iter_line_parts(geometry.GetGeometryRef(i)):
                yield part


def stream_network_geojson(vector_path, value_field='Risk', source_wkt=None,
                           cell_size=10.0, max_features=4000, precision=6,
                           feedback=None):
    """Read a SCIMAP stream network into reach-level WGS-84 GeoJSON.

    Merging and simplification happen in the layer's own projected CRS, where
    tolerances are honest metres, and only the finished reaches are reprojected.

    Every reach carries ``Risk`` (mean along the reach), ``riskNorm`` (0-1 for
    styling), ``lengthM``, ``rank`` and ``pct``, so the map, the reach inspector
    and the priority table all read from one array.
    """
    segments, layer_wkt = _read_line_segments(vector_path, value_field)
    source_wkt = source_wkt or layer_wkt
    if not source_wkt:
        raise RuntimeError(
            f"{os.path.basename(vector_path)} has no CRS, so it cannot be placed "
            "on a web map. Assign its coordinate system in QGIS and run again."
        )
    if not segments:
        return {'type': 'FeatureCollection', 'features': []}

    # Snap within 1.5 cells: enough to bridge the one-cell steps left by D8
    # vectorisation, tight enough not to weld neighbouring tributaries together.
    reaches = merge_line_network(
        segments, tolerance=max(cell_size * 1.5, 1e-6),
        simplify=max(cell_size * 0.5, 1e-6),
    )
    if feedback is not None:
        feedback.pushInfo(
            f"Stream network: {len(segments)} fragments merged into {len(reaches)} reaches."
        )

    reaches.sort(key=lambda r: (r['value'] if r['value'] is not None else -1.0,
                                r['length']), reverse=True)
    trimmed = reaches[:int(max_features)] if max_features else reaches
    if feedback is not None and len(trimmed) < len(reaches):
        feedback.pushInfo(
            f"Showing the {len(trimmed)} highest-risk reaches on the map; the full "
            "network is in the download bundle."
        )

    values = [r['value'] for r in trimmed if r['value'] is not None]
    vmin = min(values) if values else 0.0
    vmax = max(values) if values else 1.0
    span = (vmax - vmin) or 1.0

    transform = _wkt_transform(source_wkt, 4326)
    features = []
    for rank, reach in enumerate(trimmed, start=1):
        line = ogr.Geometry(ogr.wkbLineString)
        for x, y in reach['coords']:
            line.AddPoint_2D(x, y)
        if line.Transform(transform) != 0:
            continue
        coords = [[round(p[0], precision), round(p[1], precision)]
                  for p in line.GetPoints()]

        value = reach['value']
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'LineString', 'coordinates': coords},
            'properties': {
                'Risk': None if value is None else round(float(value), 6),
                'riskNorm': None if value is None else round((value - vmin) / span, 4),
                'lengthM': round(reach['length'], 1),
                'rank': rank,
                'pct': round(100.0 * (1.0 - (rank - 1) / max(len(trimmed), 1)), 1),
            },
        })

    return {'type': 'FeatureCollection', 'features': features,
            'riskRange': [vmin, vmax], 'reachCount': len(reaches)}


def _wkt_transform(source_wkt, target_epsg):
    source = osr.SpatialReference()
    source.ImportFromWkt(source_wkt)
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target = osr.SpatialReference()
    target.ImportFromEPSG(int(target_epsg))
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return osr.CoordinateTransformation(source, target)
