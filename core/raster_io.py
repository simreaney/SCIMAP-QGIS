"""GeoTIFF writing and input-grid alignment helpers."""

import os

import numpy as np
from osgeo import gdal

NODATA = -9999


def save_raster(np_array, output_path, reference_ds, data_type, mask_arr=None,
                wbt_compatible=False):
    """Write a NumPy array out via GDAL aligned to *reference_ds*."""
    # GDAL truncates to the band's dtype on write regardless, so a float64
    # array bound for a Float32 band is pure wasted RAM by the time it gets
    # here; drop it before the (also full-raster) masking step below.
    if data_type == gdal.GDT_Float32 and np_array.dtype != np.float32:
        np_array = np_array.astype(np.float32, copy=False)

    if mask_arr is not None:
        np_array = np.where(mask_arr, np_array, NODATA)
    else:
        np_array = np.nan_to_num(np_array, nan=NODATA)

    driver = gdal.GetDriverByName("GTiff")
    if wbt_compatible:
        # Some WhiteboxTools builds cannot read floating-point predictor
        # compressed GeoTIFFs (PREDICTOR=3), so write plain rasters.
        create_opts = [
            "TILED=NO",
            "COMPRESS=NONE",
            "BIGTIFF=IF_SAFER",
        ]
    else:
        predictor = "3" if data_type in (gdal.GDT_Float32, gdal.GDT_Float64) else "2"
        create_opts = [
            "TILED=YES",
            "COMPRESS=LZW",
            f"PREDICTOR={predictor}",
            "BIGTIFF=IF_SAFER",
        ]
    out_ds = driver.Create(
        output_path,
        np_array.shape[1],
        np_array.shape[0],
        1,
        data_type,
        options=create_opts,
    )
    out_ds.SetGeoTransform(reference_ds.GetGeoTransform())
    out_ds.SetProjection(reference_ds.GetProjection())
    out_ds.GetRasterBand(1).WriteArray(np_array)
    out_ds.GetRasterBand(1).SetNoDataValue(NODATA)
    out_ds.FlushCache()
    out_ds = None


def grids_match(ds_a, ds_b, tolerance=1e-9):
    """True when two datasets share size and geotransform."""
    if ds_a.RasterXSize != ds_b.RasterXSize or ds_a.RasterYSize != ds_b.RasterYSize:
        return False
    gt_a = ds_a.GetGeoTransform()
    gt_b = ds_b.GetGeoTransform()
    return all(abs(a - b) <= tolerance for a, b in zip(gt_a, gt_b))


def align_to_reference(source_path, reference_path, output_path,
                       resample='near', feedback=None, label=None):
    """Warp *source_path* onto the grid of *reference_path* if it differs.

    Returns the path that callers should read: *source_path* when the grids
    already match, otherwise the newly written *output_path*. Users supply
    arbitrary QGIS layers, so land cover and rainfall frequently arrive on a
    different grid to the DEM; the web application sidesteps this by clipping
    everything from one dataset with a shared bounding box.
    """
    ref_ds = gdal.Open(reference_path, gdal.GA_ReadOnly)
    if ref_ds is None:
        raise RuntimeError(f"Could not open reference raster: {reference_path}")
    src_ds = gdal.Open(source_path, gdal.GA_ReadOnly)
    if src_ds is None:
        raise RuntimeError(f"Could not open raster: {source_path}")

    if grids_match(ref_ds, src_ds):
        src_ds = None
        ref_ds = None
        return source_path

    gt = ref_ds.GetGeoTransform()
    width = ref_ds.RasterXSize
    height = ref_ds.RasterYSize
    bounds = (
        gt[0],
        gt[3] + gt[5] * height,
        gt[0] + gt[1] * width,
        gt[3],
    )
    projection = ref_ds.GetProjection()
    ref_ds = None
    src_ds = None

    if feedback is not None:
        feedback.pushWarning(
            f"{label or os.path.basename(source_path)} does not share the DEM's grid; "
            "resampling it onto the DEM before analysis."
        )

    gdal.Warp(
        output_path,
        source_path,
        format='GTiff',
        outputBounds=bounds,
        width=width,
        height=height,
        dstSRS=projection,
        resampleAlg=resample,
        creationOptions=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"],
    )
    if not os.path.exists(output_path):
        raise RuntimeError(f"Failed to align raster onto the DEM grid: {source_path}")
    return output_path
