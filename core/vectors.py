"""Point rasterisation, stream-network attribution and vector export helpers.

The snapping, vectorisation and export functions are ported from
``processing/wb_utils.py`` in the SCIMAP web application
(``snap_pour_point_to_max_accumulation``, ``vectorize_basin``,
``export_vector_kml``, ``raster_to_point_vector``).
"""

import logging
import os

import numpy as np
from osgeo import gdal, ogr, osr

logger = logging.getLogger(__name__)


# ── Point rasterisation ────────────────────────────────────────────────

def create_pour_point_raster(x, y, template_path, output_path):
    """Create a single-cell pour-point raster aligned to *template_path*."""
    ds_t = gdal.Open(template_path, gdal.GA_ReadOnly)
    if ds_t is None:
        raise RuntimeError(f"Could not open template raster for pour point: {template_path}")

    gt = ds_t.GetGeoTransform()
    proj = ds_t.GetProjection()
    cols = ds_t.RasterXSize
    rows = ds_t.RasterYSize
    ds_t = None

    col = int((x - gt[0]) / gt[1])
    row = int((y - gt[3]) / gt[5])
    col = max(0, min(col, cols - 1))
    row = max(0, min(row, rows - 1))

    arr = np.zeros((rows, cols), dtype=np.int8)
    arr[row, col] = 1

    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(output_path, cols, rows, 1, gdal.GDT_Byte)
    if ds is None:
        raise RuntimeError(f"Could not create pour point raster: {output_path}")
    ds.SetGeoTransform(gt)
    ds.SetProjection(proj)
    band = ds.GetRasterBand(1)
    band.WriteArray(arr)
    band.FlushCache()
    ds = None


def create_points_raster(points, template_path, output_path):
    """Create a raster with one marked source cell per (x, y) in *points*,
    aligned to *template_path*. WhiteboxTools CostDistance treats every
    non-zero cell in the source raster as a target, so multiple points
    yield the distance to whichever point is nearest."""
    ds_t = gdal.Open(template_path, gdal.GA_ReadOnly)
    if ds_t is None:
        raise RuntimeError(f"Could not open template raster for points: {template_path}")

    gt = ds_t.GetGeoTransform()
    proj = ds_t.GetProjection()
    cols = ds_t.RasterXSize
    rows = ds_t.RasterYSize
    ds_t = None

    arr = np.zeros((rows, cols), dtype=np.int8)
    for x, y in points:
        col = int((x - gt[0]) / gt[1])
        row = int((y - gt[3]) / gt[5])
        col = max(0, min(col, cols - 1))
        row = max(0, min(row, rows - 1))
        arr[row, col] = 1

    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(output_path, cols, rows, 1, gdal.GDT_Byte)
    if ds is None:
        raise RuntimeError(f"Could not create points raster: {output_path}")
    ds.SetGeoTransform(gt)
    ds.SetProjection(proj)
    band = ds.GetRasterBand(1)
    band.WriteArray(arr)
    band.FlushCache()
    ds = None


def run_wbt_cost_distance(run_wbt_func, source_path, cost_path, output_path):
    """Run WhiteboxTools CostDistance through the plugin's WBT launcher."""
    backlink_path = os.path.join(
        os.path.dirname(output_path),
        f"{os.path.splitext(os.path.basename(output_path))[0]}_backlink.tif",
    )
    run_wbt_func("CostDistance", {
        'source': source_path,
        'cost': cost_path,
        'out_accum': output_path,
        'out_backlink': backlink_path,
    })
    return backlink_path


# ── Pour-point snapping ────────────────────────────────────────────────

def snap_pour_point_to_max_accumulation(accum_path, x, y, search_radius,
                                        threshold, min_accumulation_ratio=100):
    """Snap a clicked point onto the nearest substantial flow-accumulation cell.

    Searches within *search_radius* map units for cells whose accumulation is at
    least ``max(threshold, clicked_accumulation * min_accumulation_ratio)`` and
    returns the centre of the nearest such cell, so a click that lands beside the
    channel still delineates the catchment the user meant.

    Returns ``(snapped_x, snapped_y, snapped_accumulation)``.
    """
    ds = gdal.Open(accum_path, gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"Could not open accumulation raster: {accum_path}")

    gt = ds.GetGeoTransform()
    band = ds.GetRasterBand(1)
    accum = band.ReadAsArray().astype(np.float32)
    nodata = band.GetNoDataValue()
    rows, cols = accum.shape
    ds = None

    if nodata is not None:
        accum = np.where(accum == nodata, np.nan, accum)
    accum = np.abs(accum)

    pixel_w = abs(gt[1])
    pixel_h = abs(gt[5])

    col = int((x - gt[0]) / gt[1])
    row = int((y - gt[3]) / gt[5])
    if not (0 <= col < cols and 0 <= row < rows):
        raise RuntimeError(
            "The pour point falls outside the DEM. Pick a point inside the "
            "elevation model's extent."
        )

    clicked = accum[row, col]
    effective_threshold = float(threshold)
    if np.isfinite(clicked) and clicked > 0:
        effective_threshold = max(effective_threshold, clicked * float(min_accumulation_ratio))

    radius_cols = max(1, int(round(search_radius / pixel_w)))
    radius_rows = max(1, int(round(search_radius / pixel_h)))

    r0 = max(0, row - radius_rows)
    r1 = min(rows, row + radius_rows + 1)
    c0 = max(0, col - radius_cols)
    c1 = min(cols, col + radius_cols + 1)

    window = accum[r0:r1, c0:c1]
    candidates = np.isfinite(window) & (window >= effective_threshold)

    if not candidates.any():
        # Nothing large enough nearby; fall back to the local maximum so the
        # delineation still produces a basin rather than failing outright.
        if not np.isfinite(window).any():
            return float(x), float(y), float('nan')
        local = int(np.argmax(np.where(np.isfinite(window), window, -np.inf)))
        best_r, best_c = np.unravel_index(local, window.shape)
    else:
        rr, cc = np.nonzero(candidates)
        d_rows = (rr + r0 - row) * pixel_h
        d_cols = (cc + c0 - col) * pixel_w
        distances = np.hypot(d_rows, d_cols)
        nearest = int(np.argmin(distances))
        best_r, best_c = rr[nearest], cc[nearest]

    abs_row = int(best_r + r0)
    abs_col = int(best_c + c0)

    snapped_x = gt[0] + (abs_col + 0.5) * gt[1] + (abs_row + 0.5) * gt[2]
    snapped_y = gt[3] + (abs_col + 0.5) * gt[4] + (abs_row + 0.5) * gt[5]
    return float(snapped_x), float(snapped_y), float(accum[abs_row, abs_col])


# ── Vectorisation ──────────────────────────────────────────────────────

def vectorize_basin(basin_path, output_path, min_area=0.0, output_format="GPKG",
                    layer_name="catchment"):
    """Polygonise a watershed raster and dissolve it into a single multipolygon.

    Parts smaller than *min_area* (map units squared) are dropped, which removes
    the speckle WhiteboxTools leaves around basin edges.
    """
    ds = gdal.Open(basin_path, gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"Could not open basin raster: {basin_path}")
    band = ds.GetRasterBand(1)
    arr = band.ReadAsArray().astype(np.float32)
    nodata = band.GetNoDataValue()
    projection = ds.GetProjection()
    geotransform = ds.GetGeoTransform()
    x_size, y_size = ds.RasterXSize, ds.RasterYSize
    ds = None

    valid = np.isfinite(arr) & (arr > 0)
    if nodata is not None:
        valid = valid & (arr != nodata)
    if not valid.any():
        raise RuntimeError(
            "Watershed delineation produced an empty basin. Try increasing the "
            "snap search radius or picking a point closer to the channel network."
        )

    # Polygonise a clean 0/1 mask so we get one part per basin, not one per value.
    mem_drv = gdal.GetDriverByName("MEM")
    mask_ds = mem_drv.Create('', x_size, y_size, 1, gdal.GDT_Byte)
    mask_ds.SetGeoTransform(geotransform)
    mask_ds.SetProjection(projection)
    mask_band = mask_ds.GetRasterBand(1)
    mask_band.WriteArray(valid.astype(np.uint8))

    srs = osr.SpatialReference()
    if projection:
        srs.ImportFromWkt(projection)

    vec_drv = ogr.GetDriverByName("Memory")
    vec_ds = vec_drv.CreateDataSource('polygonised')
    vec_layer = vec_ds.CreateLayer('parts', srs=srs, geom_type=ogr.wkbPolygon)
    vec_layer.CreateField(ogr.FieldDefn("value", ogr.OFTInteger))

    gdal.Polygonize(mask_band, mask_band, vec_layer, 0, [], callback=None)
    mask_ds = None

    merged = ogr.Geometry(ogr.wkbMultiPolygon)
    vec_layer.ResetReading()
    for feature in vec_layer:
        if feature.GetField("value") != 1:
            continue
        geom = feature.GetGeometryRef()
        if geom is None:
            continue
        if min_area and geom.GetArea() < min_area:
            continue
        merged.AddGeometry(geom.Clone())
    vec_ds = None

    if merged.GetGeometryCount() == 0:
        raise RuntimeError(
            "Watershed delineation produced no polygon above the minimum area."
        )

    dissolved = merged.UnionCascaded() or merged
    if dissolved.GetGeometryName() == "POLYGON":
        multi = ogr.Geometry(ogr.wkbMultiPolygon)
        multi.AddGeometry(dissolved)
        dissolved = multi

    area = dissolved.GetArea()
    _write_single_geometry(
        output_path, dissolved, srs, output_format, layer_name,
        {"area_m2": float(area)},
    )
    return area


def write_point_vector(x, y, output_path, srs_wkt, attributes=None,
                       output_format="GPKG", layer_name="pour_point"):
    """Write a single point feature, used for the snapped pour point."""
    srs = osr.SpatialReference()
    if srs_wkt:
        srs.ImportFromWkt(srs_wkt)

    point = ogr.Geometry(ogr.wkbPoint)
    point.AddPoint(float(x), float(y))
    _write_single_geometry(
        output_path, point, srs, output_format, layer_name, attributes or {},
    )


def _write_single_geometry(output_path, geometry, srs, output_format, layer_name,
                           attributes=None):
    driver = ogr.GetDriverByName(_driver_for(output_path, output_format))
    if driver is None:
        raise RuntimeError(f"Could not load OGR driver for {output_path}")
    if os.path.exists(output_path):
        driver.DeleteDataSource(output_path)

    out_ds = driver.CreateDataSource(output_path)
    if out_ds is None:
        raise RuntimeError(f"Could not create output vector: {output_path}")

    out_layer = out_ds.CreateLayer(layer_name, srs=srs, geom_type=geometry.GetGeometryType())
    attributes = attributes or {}
    for field_name, value in attributes.items():
        field_type = ogr.OFTReal if isinstance(value, float) else ogr.OFTString
        out_layer.CreateField(ogr.FieldDefn(field_name[:10], field_type))

    feature = ogr.Feature(out_layer.GetLayerDefn())
    for field_name, value in attributes.items():
        feature.SetField(field_name[:10], value)
    feature.SetGeometry(geometry)
    out_layer.CreateFeature(feature)
    feature = None
    out_ds = None


def _driver_for(path, fallback):
    ext = os.path.splitext(path)[1].lower()
    return {
        '.gpkg': 'GPKG',
        '.shp': 'ESRI Shapefile',
        '.geojson': 'GeoJSON',
        '.json': 'GeoJSON',
        '.kml': 'KML',
    }.get(ext, fallback)


# ── Exports ────────────────────────────────────────────────────────────

def export_vector_kml(input_path, output_kml, source_wkt=None, source_epsg=None,
                      layer_name="streams"):
    """Export a vector layer to KML, reprojecting to WGS-84.

    Unlike the web application's version this takes the source CRS from the layer
    itself rather than assuming British National Grid, since plugin users supply
    their own data.
    """
    in_ds = ogr.Open(input_path)
    if in_ds is None:
        raise RuntimeError(f"Could not open vector for KML export: {input_path}")
    in_layer = in_ds.GetLayer(0)

    src_srs = osr.SpatialReference()
    if source_wkt:
        src_srs.ImportFromWkt(source_wkt)
    elif source_epsg:
        src_srs.ImportFromEPSG(int(source_epsg))
    else:
        layer_srs = in_layer.GetSpatialRef()
        if layer_srs is None:
            raise RuntimeError(
                f"{os.path.basename(input_path)} has no CRS, so it cannot be "
                "reprojected to WGS-84 for KML export."
            )
        src_srs = layer_srs.Clone()
    src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    dst_srs = osr.SpatialReference()
    dst_srs.ImportFromEPSG(4326)
    dst_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    ct = osr.CoordinateTransformation(src_srs, dst_srs)

    driver = ogr.GetDriverByName("KML")
    if os.path.exists(output_kml):
        os.remove(output_kml)
    out_ds = driver.CreateDataSource(output_kml)
    out_layer = out_ds.CreateLayer(layer_name, dst_srs, in_layer.GetGeomType())

    in_defn = in_layer.GetLayerDefn()
    for idx in range(in_defn.GetFieldCount()):
        out_layer.CreateField(in_defn.GetFieldDefn(idx))
    out_defn = out_layer.GetLayerDefn()

    # The KML driver adds its own Name/Description fields and may drop others,
    # so field indices do not line up between source and destination. Pair them
    # up by name and copy only the fields that exist on both sides.
    field_pairs = []
    for src_idx in range(in_defn.GetFieldCount()):
        field_name = in_defn.GetFieldDefn(src_idx).GetNameRef()
        dst_idx = out_defn.GetFieldIndex(field_name)
        if dst_idx >= 0:
            field_pairs.append((src_idx, dst_idx))

    in_layer.ResetReading()
    for feat in in_layer:
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        geom = geom.Clone()
        geom.Transform(ct)
        out_feat = ogr.Feature(out_defn)
        for src_idx, dst_idx in field_pairs:
            if feat.IsFieldSet(src_idx) and not feat.IsFieldNull(src_idx):
                out_feat.SetField(dst_idx, feat.GetField(src_idx))
        out_feat.SetGeometry(geom)
        out_layer.CreateFeature(out_feat)
        out_feat = None

    out_ds = None
    in_ds = None


def raster_to_point_vector(raster_path, output_path, field_name="scimap_risk",
                           output_format="GPKG", positive_only=False,
                           layer_name=None):
    """Convert valid raster cells to point features carrying the cell value.

    A point is created at the centre of every valid pixel, which for a SCIMAP
    output raster means one point per stream cell carrying its risk score.
    """
    raster_ds = gdal.Open(raster_path, gdal.GA_ReadOnly)
    if raster_ds is None:
        raise FileNotFoundError(f"Cannot open raster: {raster_path}")

    band = raster_ds.GetRasterBand(1)
    arr = band.ReadAsArray().astype(np.float32)
    nodata = band.GetNoDataValue()
    gt = raster_ds.GetGeoTransform()
    proj = raster_ds.GetProjection()
    raster_ds = None

    valid = np.isfinite(arr)
    if nodata is not None:
        nd = float(nodata)
        if np.isfinite(nd):
            valid = valid & (np.abs(arr - nd) > 1e-3)
    if positive_only:
        valid = valid & (arr > 0)

    n_valid = int(np.count_nonzero(valid))
    logger.info("raster_to_point_vector: %d valid pixels in %s", n_valid, raster_path)

    # Truncate field name to 10 chars (shapefile limit)
    safe_field = field_name[:10]

    r_idx, c_idx = np.nonzero(valid)
    xs = gt[0] + (c_idx + 0.5) * gt[1] + (r_idx + 0.5) * gt[2]
    ys = gt[3] + (c_idx + 0.5) * gt[4] + (r_idx + 0.5) * gt[5]
    vals = arr[r_idx, c_idx].astype(np.float64)

    srs = osr.SpatialReference()
    if proj:
        srs.ImportFromWkt(proj)

    driver = ogr.GetDriverByName(_driver_for(output_path, output_format))
    if driver is None:
        raise RuntimeError(f"Could not load OGR driver for {output_path}")
    if os.path.exists(output_path):
        driver.DeleteDataSource(output_path)

    out_ds = driver.CreateDataSource(output_path)
    if out_ds is None:
        raise RuntimeError(f"Could not create output vector: {output_path}")

    if layer_name is None:
        layer_name = os.path.splitext(os.path.basename(output_path))[0]
    out_layer = out_ds.CreateLayer(layer_name, srs=srs, geom_type=ogr.wkbPoint)
    out_layer.CreateField(ogr.FieldDefn(safe_field, ogr.OFTReal))
    out_defn = out_layer.GetLayerDefn()

    out_layer.StartTransaction()
    for x, y, value in zip(xs, ys, vals):
        feature = ogr.Feature(out_defn)
        feature.SetField(safe_field, float(value))
        point = ogr.Geometry(ogr.wkbPoint)
        point.AddPoint(float(x), float(y))
        feature.SetGeometry(point)
        out_layer.CreateFeature(feature)
        feature = None
    out_layer.CommitTransaction()
    out_ds = None

    return n_valid


def write_cog(source_raster, output_path, resample='NEAREST'):
    """Rewrite a GeoTIFF as a Cloud-Optimised GeoTIFF."""
    gdal.Translate(
        output_path,
        source_raster,
        format='COG',
        creationOptions=[
            'COMPRESS=LZW',
            'BLOCKSIZE=512',
            f'RESAMPLING={resample}',
        ],
    )
    if not os.path.exists(output_path):
        raise RuntimeError(f"Failed to write COG: {output_path}")
    return output_path


def attribute_stream_network(vector_path, value_array, geotransform, field_name="Risk"):
    """Add a field to each stream line holding the mean raster value along it."""
    ds = ogr.Open(vector_path, 1)
    if ds is None:
        return
    layer = ds.GetLayer()

    layer.CreateField(ogr.FieldDefn(field_name, ogr.OFTReal))
    inv_gt = gdal.InvGeoTransform(geotransform)

    for feat in layer:
        geom = feat.GetGeometryRef()
        vals = []

        if geom is None:
            continue
        sub_geoms = (
            [geom] if geom.GetGeometryName() == "LINESTRING"
            else [geom.GetGeometryRef(i) for i in range(geom.GetGeometryCount())]
        )

        for sub_geom in sub_geoms:
            pts = sub_geom.GetPoints()
            if pts:
                for pt in pts:
                    x, y = pt[0], pt[1]
                    col, row = gdal.ApplyGeoTransform(inv_gt, x, y)
                    col, row = int(col), int(row)
                    if 0 <= row < value_array.shape[0] and 0 <= col < value_array.shape[1]:
                        v = value_array[row, col]
                        if np.isfinite(v):
                            vals.append(v)

        if vals:
            feat.SetField(field_name, float(np.mean(vals)))
        layer.SetFeature(feat)

    ds = None


def copy_vector(source_path, output_path, layer_name=None, output_format="GPKG"):
    """Copy an OGR datasource to another path/format, preserving fields."""
    src_ds = ogr.Open(source_path, 0)
    if src_ds is None:
        raise RuntimeError(f"Could not open vector for export: {source_path}")

    src_layer = src_ds.GetLayer(0)
    if src_layer is None:
        raise RuntimeError(f"Vector contains no layers: {source_path}")

    driver_name = _driver_for(output_path, output_format)
    is_gpkg = driver_name == "GPKG"
    if layer_name is None:
        layer_name = "streams" if is_gpkg else os.path.splitext(os.path.basename(output_path))[0]

    driver = ogr.GetDriverByName(driver_name)
    if driver is None:
        raise RuntimeError(f"Could not load OGR driver: {driver_name}")

    if os.path.exists(output_path):
        driver.DeleteDataSource(output_path)

    dst_ds = driver.CreateDataSource(output_path)
    if dst_ds is None:
        raise RuntimeError(f"Could not create output vector: {output_path}")

    dst_layer = dst_ds.CreateLayer(
        layer_name, srs=src_layer.GetSpatialRef(), geom_type=src_layer.GetGeomType(),
    )
    if dst_layer is None:
        raise RuntimeError(f"Could not create output layer in vector file: {output_path}")

    src_defn = src_layer.GetLayerDefn()
    for idx in range(src_defn.GetFieldCount()):
        src_field_defn = src_defn.GetFieldDefn(idx)
        src_field_name = src_field_defn.GetNameRef()
        if is_gpkg and src_field_name and src_field_name.lower() in {"fid", "ogc_fid"}:
            continue
        dst_layer.CreateField(src_field_defn)

    dst_defn = dst_layer.GetLayerDefn()
    for src_feat in src_layer:
        dst_feat = ogr.Feature(dst_defn)
        dst_feat.SetFID(-1)

        for idx in range(dst_defn.GetFieldCount()):
            field_name = dst_defn.GetFieldDefn(idx).GetNameRef()
            src_idx = src_defn.GetFieldIndex(field_name)
            if src_idx >= 0:
                dst_feat.SetField(idx, src_feat.GetField(src_idx))

        geom = src_feat.GetGeometryRef()
        if geom is not None:
            dst_feat.SetGeometry(geom.Clone())
        dst_layer.CreateFeature(dst_feat)
        dst_feat = None

    dst_ds = None
    src_ds = None
