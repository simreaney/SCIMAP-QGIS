"""SCIMAP Flood — spatial targeting of nature-based solutions.

Implements the algorithm from Reaney, Sim M. (2022) "Spatial targeting of
nature-based solutions for flood risk management within river catchments",
Journal of Flood Risk Management, e12803.

Combines connectivity, runoff generation potential, an ensemble of rainfall
patterns and per-impact-point overland flow distance, returning the mean and
standard deviation across every rainfall x impact-point combination.
"""

import os
import shutil
import tempfile

import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsProcessing,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
)

from ..core import flood_windowed, styling
from ..data.defaults import RAMP_FLOOD
from ..localization import tr
from .base import ScimapAlgorithmBase, ScimapCanceled


class ScimapFloodAlgorithm(ScimapAlgorithmBase):
    """SCIMAP Flood - operates on pre-computed input rasters.

    Mirrors core.py's SCIMAPFlood exactly:
      - connectivity_map  → pre-computed connectivity raster
      - runoff_map        → pre-computed runoff / land cover weights raster
      - rainfall_maps     → multiple rainfall pattern rasters (all are used)
      - overland_flow_maps → pre-computed overland flow distance rasters

    Every input raster has its declared nodata value, and any negative
    value, treated as invalid (NaN).

    Algorithm:
        base = connectivity × runoff   (raw, unscaled)
        rainfall_norm = (rainfall - raster_min) / (raster_max - raster_min)
        overland_norm = peak-at-median stretch to [0, 1] using the
            raster's min, median and max (1.0 at the median, falling off
            linearly to 0.0 at the min/max)
        For each (rainfall, overland) combination:
            product = base × rainfall_norm × overland_norm
        mean  = per-pixel mean(product) over combinations valid at that pixel
        stdev = per-pixel population stdev(product) over the same set
        output mean is finally divided by its own global maximum

    Reference:
        Reaney, Sim M. (2022) Spatial targeting of nature-based solutions
        for flood risk management within river catchments.
        Journal of Flood Risk Management e12803.
    """

    INPUT_CONNECTIVITY = 'INPUT_CONNECTIVITY'
    INPUT_RUNOFF = 'INPUT_RUNOFF'
    INPUT_RAINFALL_MAPS = 'INPUT_RAINFALL_MAPS'
    INPUT_OFD_MAPS = 'INPUT_OFD_MAPS'

    OUT_MEAN = 'OUT_MEAN'
    OUT_STDEV = 'OUT_STDEV'

    def createInstance(self):
        return ScimapFloodAlgorithm()

    def name(self):
        return 'scimapflood'

    def displayName(self):
        return tr('SCIMAP Flood')

    _ICON = 'icon_flood.svg'

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_CONNECTIVITY,
            tr('Connectivity Raster (pre-computed)')))

        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_RUNOFF,
            tr('Runoff / Land Cover Weights Raster (pre-computed)')))

        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.INPUT_RAINFALL_MAPS,
            tr('Rainfall Pattern Rasters'),
            layerType=QgsProcessing.TypeRaster,
        ))

        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.INPUT_OFD_MAPS,
            tr('Overland Flow Distance Rasters (pre-computed)'),
            layerType=QgsProcessing.TypeRaster,
        ))

        self.add_colour_ramp_parameter()

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_MEAN, tr('SCIMAP-Flood Mean')))

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_STDEV, tr('SCIMAP-Flood Standard Deviation')))

    def processAlgorithm(self, parameters, context, feedback):
        # ── Citation (mirrors pySCIMAP-Flood_2026.py print statements) ─
        feedback.pushInfo('SCIMAP-Flood. Reaney 2022-2026')
        feedback.pushInfo(
            'Reaney, Sim M. (2022) Spatial targeting of nature-based solutions '
            'for flood risk management within river catchments. '
            'Journal of Flood Risk Management e12803'
        )
        feedback.pushInfo('')

        conn_layer = self.parameterAsRasterLayer(parameters, self.INPUT_CONNECTIVITY, context)
        runoff_layer = self.parameterAsRasterLayer(parameters, self.INPUT_RUNOFF, context)
        rainfall_layers = self.parameterAsLayerList(parameters, self.INPUT_RAINFALL_MAPS, context) or []
        ofd_layers = self.parameterAsLayerList(parameters, self.INPUT_OFD_MAPS, context) or []

        out_mean_path = self.parameterAsOutputLayer(parameters, self.OUT_MEAN, context)
        out_stdev_path = self.parameterAsOutputLayer(parameters, self.OUT_STDEV, context)

        if out_mean_path == out_stdev_path:
            raise RuntimeError('Output rasters must use different file paths.')
        if not rainfall_layers:
            raise RuntimeError('At least one rainfall pattern raster is required.')
        if not ofd_layers:
            raise RuntimeError('At least one overland flow distance raster is required.')

        # This runs window-by-window (chunk_size x chunk_size tiles) rather
        # than loading every input raster fully into memory, mirroring
        # core.py's rasterio-based windowed SCIMAP-Flood implementation. Only
        # one tile's worth of connectivity/runoff/rainfall/OFD data is ever
        # resident at once, so peak RAM no longer scales with raster size.
        chunk_size = flood_windowed.FLOOD_DEFAULT_CHUNK_SIZE

        # ── 1. Open connectivity raster ──────────────────────────────────
        feedback.setProgress(2)
        feedback.pushInfo('1. Opening connectivity raster...')
        conn_ds = gdal.Open(conn_layer.source())
        if conn_ds is None:
            raise RuntimeError(f'Could not open connectivity raster: {conn_layer.source()}')
        conn_band = conn_ds.GetRasterBand(1)
        conn_nodata = conn_band.GetNoDataValue()

        reference_gt = conn_ds.GetGeoTransform()
        reference_size = (conn_ds.RasterXSize, conn_ds.RasterYSize)
        width, height = reference_size

        open_datasets = [conn_ds]

        def _open_aligned_raster(layer, label):
            """Open a raster and verify it matches the reference grid, without reading it."""
            source = layer.source() if hasattr(layer, 'source') else str(layer)
            ds = gdal.Open(source)
            if ds is None:
                raise RuntimeError(f'Could not open {label}: {source}')
            if ds.RasterXSize != reference_size[0] or ds.RasterYSize != reference_size[1]:
                raise RuntimeError(f'{label} does not match the connectivity grid size.')
            gt = ds.GetGeoTransform()
            if any(abs(float(gt[i]) - float(reference_gt[i])) > 1e-9 for i in range(6)):
                raise RuntimeError(f'{label} does not match the connectivity grid alignment.')
            open_datasets.append(ds)
            return ds

        # ── 2. Open runoff / land cover weights raster ───────────────────
        # Used as-is, matching core.py (`base = connectivity * runoff`, no
        # percentile rescaling).
        feedback.setProgress(8)
        feedback.pushInfo('2. Opening runoff / land cover weights raster...')
        runoff_ds = _open_aligned_raster(runoff_layer, 'runoff raster')
        runoff_band = runoff_ds.GetRasterBand(1)
        runoff_nodata = runoff_band.GetNoDataValue()

        # ── 3. Scan rainfall patterns for their global min/max ───────────
        feedback.setProgress(15)
        feedback.pushInfo('3. Scanning rainfall pattern rasters for min/max...')
        rainfall_bands = []
        rainfall_nodata = []
        rf_min_max = []
        for idx, layer in enumerate(rainfall_layers):
            ds = _open_aligned_raster(layer, f'rainfall pattern raster {idx + 1}')
            band = ds.GetRasterBand(1)
            nodata = band.GetNoDataValue()
            rf_min, rf_max = flood_windowed._flood_stream_min_max(band, nodata, width, height, chunk_size)
            if not np.isfinite(rf_min) or not np.isfinite(rf_max):
                raise RuntimeError(f'Rainfall pattern raster {idx + 1} has no valid data.')
            if rf_max <= rf_min:
                raise RuntimeError(
                    f'Rainfall pattern raster {idx + 1} has no valid data range (min == max).'
                )
            rainfall_bands.append(band)
            rainfall_nodata.append(nodata)
            rf_min_max.append((rf_min, rf_max))

        # ── 4. Scan overland flow distance rasters for their global stats ─
        feedback.setProgress(30)
        feedback.pushInfo(
            f'4. Scanning {len(ofd_layers)} overland flow distance raster(s) for min/median/max...'
        )
        ofd_bands = []
        ofd_nodata = []
        ofd_stats = []
        for idx, layer in enumerate(ofd_layers):
            feedback.pushInfo(
                f'Scanning overland flow distance raster {idx + 1}/{len(ofd_layers)}'
            )
            ds = _open_aligned_raster(layer, f'OFD raster {idx + 1}')
            band = ds.GetRasterBand(1)
            nodata = band.GetNoDataValue()
            ofd_min, ofd_max = flood_windowed._flood_stream_min_max(band, nodata, width, height, chunk_size)
            if not np.isfinite(ofd_min) or not np.isfinite(ofd_max):
                raise RuntimeError(f'OFD raster {idx + 1} has no valid data.')
            if ofd_max <= ofd_min:
                raise RuntimeError(
                    f'OFD raster {idx + 1} has no valid data range (min == max).'
                )
            ofd_median = flood_windowed._flood_stream_median(band, nodata, width, height, chunk_size)
            if ofd_median == 0:
                raise RuntimeError(f'OFD raster {idx + 1} has an invalid (zero) median.')
            ofd_bands.append(band)
            ofd_nodata.append(nodata)
            ofd_stats.append((ofd_min, ofd_median, ofd_max))

        # ── 5. SCIMAP-Flood calculation: mean + stdev, windowed ───────────
        # Mirrors core.py exactly: base = connectivity * runoff; for every
        # (overland, rainfall) combination, product = base * rainfall_norm *
        # overland_norm; mean/stdev are accumulated per-pixel so only
        # combinations that are valid at a given pixel contribute there.
        # Unlike the previous implementation this now runs one window at a
        # time; raw (unnormalised) mean/stdev are staged to temporary GDAL
        # rasters, since the mean can only be normalised by its own global
        # maximum once every window has been visited.
        n_combos = len(ofd_bands) * len(rainfall_bands)
        feedback.pushInfo(
            f'5. Running windowed SCIMAP-Flood calculation over {n_combos} combination(s) '
            f'({len(rainfall_bands)} rainfall × {len(ofd_bands)} OFD, '
            f'{chunk_size}x{chunk_size} tiles)...'
        )

        tmp_dir = tempfile.mkdtemp(prefix='scimap_flood_')
        driver = gdal.GetDriverByName('GTiff')
        create_opts = ['TILED=YES', 'COMPRESS=LZW', 'PREDICTOR=3', 'BIGTIFF=IF_SAFER']

        mean_tmp_path = os.path.join(tmp_dir, 'mean_tmp.tif')
        stdev_tmp_path = os.path.join(tmp_dir, 'stdev_tmp.tif')

        mean_tmp_ds = driver.Create(mean_tmp_path, width, height, 1, gdal.GDT_Float32, options=create_opts)
        mean_tmp_ds.SetGeoTransform(reference_gt)
        mean_tmp_ds.SetProjection(conn_ds.GetProjection())
        mean_tmp_band = mean_tmp_ds.GetRasterBand(1)
        mean_tmp_band.SetNoDataValue(float('nan'))

        stdev_tmp_ds = driver.Create(stdev_tmp_path, width, height, 1, gdal.GDT_Float32, options=create_opts)
        stdev_tmp_ds.SetGeoTransform(reference_gt)
        stdev_tmp_ds.SetProjection(conn_ds.GetProjection())
        stdev_tmp_band = stdev_tmp_ds.GetRasterBand(1)
        stdev_tmp_band.SetNoDataValue(float('nan'))

        def _write_mean_window(window, array):
            col_off, row_off, _, _ = window
            mean_tmp_band.WriteArray(array, col_off, row_off)

        def _write_stdev_window(window, array):
            col_off, row_off, _, _ = window
            stdev_tmp_band.WriteArray(array, col_off, row_off)

        def _main_pass_progress(done, total):
            feedback.setProgress(35 + int(45 * done / max(total, 1)))

        try:
            mean_max = flood_windowed._run_scimap_flood_windowed(
                conn_band, conn_nodata,
                runoff_band, runoff_nodata,
                rainfall_bands, rainfall_nodata, rf_min_max,
                ofd_bands, ofd_nodata, ofd_stats,
                width, height,
                _write_mean_window, _write_stdev_window,
                chunk_size=chunk_size,
                progress_callback=_main_pass_progress,
            )
        except RuntimeError:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

        mean_tmp_band.FlushCache()
        stdev_tmp_band.FlushCache()

        # ── 6. Normalise mean output by its global maximum, windowed ─────
        feedback.setProgress(85)
        feedback.pushInfo('6. Normalising mean output and writing final rasters...')

        mean_out_ds = driver.Create(out_mean_path, width, height, 1, gdal.GDT_Float32, options=create_opts)
        mean_out_ds.SetGeoTransform(reference_gt)
        mean_out_ds.SetProjection(conn_ds.GetProjection())
        mean_out_band = mean_out_ds.GetRasterBand(1)
        mean_out_band.SetNoDataValue(-9999)

        stdev_out_ds = driver.Create(out_stdev_path, width, height, 1, gdal.GDT_Float32, options=create_opts)
        stdev_out_ds.SetGeoTransform(reference_gt)
        stdev_out_ds.SetProjection(conn_ds.GetProjection())
        stdev_out_band = stdev_out_ds.GetRasterBand(1)
        stdev_out_band.SetNoDataValue(-9999)

        for window in flood_windowed._flood_iter_windows(width, height, chunk_size):
            col_off, row_off, win_width, win_height = window

            mean_window = mean_tmp_band.ReadAsArray(col_off, row_off, win_width, win_height)
            mean_norm_window = np.where(
                np.isfinite(mean_window), mean_window / mean_max, np.nan
            ).astype(np.float32, copy=False)
            mean_norm_window = np.nan_to_num(mean_norm_window, nan=-9999)
            mean_out_band.WriteArray(mean_norm_window, col_off, row_off)

            stdev_window = stdev_tmp_band.ReadAsArray(col_off, row_off, win_width, win_height)
            stdev_window = np.nan_to_num(stdev_window, nan=-9999)
            stdev_out_band.WriteArray(stdev_window, col_off, row_off)

        mean_out_band.FlushCache()
        stdev_out_band.FlushCache()
        mean_out_ds = None
        stdev_out_ds = None

        mean_tmp_ds = None
        stdev_tmp_ds = None
        shutil.rmtree(tmp_dir, ignore_errors=True)

        feedback.setProgress(100)
        feedback.pushInfo('SCIMAP-Flood complete.')
        feedback.pushInfo('Saved outputs:')
        feedback.pushInfo(f'- mean: {out_mean_path}')
        feedback.pushInfo(f'- stdev: {out_stdev_path}')

        ramp = self.colour_ramp_name(parameters, context, RAMP_FLOOD)
        styling.style_output(context, out_mean_path, ramp)
        styling.style_output(context, out_stdev_path, ramp)

        return {
            self.OUT_MEAN: out_mean_path,
            self.OUT_STDEV: out_stdev_path,
        }
