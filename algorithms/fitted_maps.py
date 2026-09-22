"""SCIMAP Fitted 3: Ensemble Risk Maps — Step 4 of the research scripts.

Propagates calibrated land-cover weight sets back onto the raster grid and
summarises the resulting ensemble, so the output carries its own uncertainty
rather than presenting one weight set as the answer.

The cost is dominated by flow routing, so the linearity trick in
``core/ensemble.py`` is what makes this feasible: risk is linear in the
weights and routing is linear, so routing runs once per *land-cover class*
(seven times) rather than once per weight set (thirty or more), and every
weight set's map is then a weighted sum of the per-class routed rasters.

Outputs:

* mean / standard deviation, and median / inter-quartile range, of the routed
  and diluted risk over the whole grid;
* the same median / IQR for the *local* risk, before routing;
* two "no-regrets" rasters giving, per cell, the percentage of combinations
  that put it in their own top tier — cells worth acting on whichever
  calibrated weight set turns out to be right;
* a river-network point layer and a per-combination summary table.
"""

import os
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsProcessing,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorDestination,
)

from ..core import ensemble, fitted_io, plotting, raster_io, styling, vectors
from ..core.erosion import percentile_range
from ..data.defaults import RAMP_FITTED, RAMP_UNCERTAINTY
from ..localization import tr
from .base import ScimapCanceled
from .fitted_base import CollectingFeedback, ScimapFittedAlgorithmBase

_PERCENTILE_SAMPLE_CAP = 5_000_000
_ROUTING_TOOLS = ('DInfMassFlux', 'D8MassFlux')


class ScimapFittedMapsAlgorithm(ScimapFittedAlgorithmBase):
    """Ensemble risk maps from calibrated land-cover weight sets."""

    INPUT_WEIGHTS = 'INPUT_WEIGHTS'
    N_WEIGHT_SETS = 'N_WEIGHT_SETS'
    INPUT_CONNECTIVITY = 'INPUT_CONNECTIVITY'
    INPUT_LC = 'INPUT_LC'
    INPUT_EROSION = 'INPUT_EROSION'
    INPUT_DEM = 'INPUT_DEM'
    BREACH_DEPRESSIONS = 'BREACH_DEPRESSIONS'
    INPUT_FLOW_ACCUM = 'INPUT_FLOW_ACCUM'
    INPUT_RAINFALL_AREA = 'INPUT_RAINFALL_AREA'

    ROUTING_TOOL = 'ROUTING_TOOL'
    MIN_UPSLOPE_AREA_KM2 = 'MIN_UPSLOPE_AREA_KM2'
    MIN_RAINFALL_AREA_PERCENTILE = 'MIN_RAINFALL_AREA_PERCENTILE'
    NO_REGRETS_TOP_PERCENT = 'NO_REGRETS_TOP_PERCENT'
    MAX_WORKERS_ROUTING = 'MAX_WORKERS_ROUTING'
    MAX_WORKERS_BLOCKS = 'MAX_WORKERS_BLOCKS'
    MEMORY_BUDGET_MB = 'MEMORY_BUDGET_MB'
    KEEP_INTERMEDIATE = 'KEEP_INTERMEDIATE'

    OUT_MEAN_STDEV = 'OUT_MEAN_STDEV'
    OUT_MEDIAN_IQR = 'OUT_MEDIAN_IQR'
    OUT_LOCAL_MEDIAN_IQR = 'OUT_LOCAL_MEDIAN_IQR'
    OUT_NO_REGRETS_INCHANNEL = 'OUT_NO_REGRETS_INCHANNEL'
    OUT_NO_REGRETS_LANDSCAPE = 'OUT_NO_REGRETS_LANDSCAPE'
    OUT_NETWORK_POINTS = 'OUT_NETWORK_POINTS'
    OUT_RESULTS_CSV = 'OUT_RESULTS_CSV'
    OUT_HISTOGRAMS = 'OUT_HISTOGRAMS'

    _ICON = 'icon_fitted.svg'

    def createInstance(self):
        return ScimapFittedMapsAlgorithm()

    def name(self):
        return 'fittedmaps'

    def displayName(self):
        return tr('SCIMAP Fitted 3: Ensemble Risk Maps')

    def shortHelpString(self):
        return tr(
            'Turns calibrated land-cover weight sets into risk maps that carry '
            'their own uncertainty.\n\n'
            'Every weight set is propagated across the catchment, routed down '
            'the flow network and diluted by the rainfall-weighted contributing '
            'area. The ensemble is then summarised as mean/standard deviation '
            'and median/inter-quartile range, so spread is visible instead of '
            'one weight set standing in for the answer.\n\n'
            'The "no-regrets" outputs give, for each cell, the percentage of '
            'weight sets that place it in their own top tier. Cells scoring '
            'high there are worth acting on whichever calibration is correct.\n\n'
            'This is the slow step: it runs one flow-routing pass per land-cover '
            'class, per connectivity and land-cover input. Feed it the optional '
            'raster outputs of "SCIMAP Fitted 1: Catchment Statistics" so it does '
            'not recompute hydrology, and expect tens of minutes on a large '
            'grid.\n\n'
            'Every input raster must already share one grid; nothing is '
            'resampled here.\n\n'
            'Unlike the research scripts this writes a styled point layer rather '
            'than a rendered basemap image, so the network can be zoomed and '
            'queried over your own basemap. Colour it by "median" and drive '
            'opacity from "iqr" to see risk and confidence together.'
        )

    # ── Parameters ──────────────────────────────────────────────────────

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile(
            self.INPUT_WEIGHTS,
            tr('Calibrated weight sets (CSV)'),
            behavior=QgsProcessingParameterFile.File,
            extension='csv', fileFilter='CSV files (*.csv)'))

        self.addParameter(QgsProcessingParameterNumber(
            self.N_WEIGHT_SETS,
            tr('Weight sets to propagate (0 = all)'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=30, minValue=0))

        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.INPUT_CONNECTIVITY, tr('Connectivity raster(s)'),
            layerType=QgsProcessing.TypeRaster))

        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.INPUT_LC, tr('Land cover raster(s)'),
            layerType=QgsProcessing.TypeRaster))
        self.add_landcover_scheme_parameters()

        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_EROSION, tr('Erosion potential raster'), optional=True))

        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_DEM, tr('DEM for flow routing (breached)')))
        self.addParameter(QgsProcessingParameterBoolean(
            self.BREACH_DEPRESSIONS,
            tr('Breach depressions first (tick if supplying a raw DEM)'),
            defaultValue=False))

        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_FLOW_ACCUM,
            tr('D8 flow accumulation, cells (optional)'), optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_RAINFALL_AREA,
            tr('Rainfall-weighted contributing area (optional)'), optional=True))

        self.addParameter(QgsProcessingParameterEnum(
            self.ROUTING_TOOL,
            tr('Flow routing'),
            options=[
                tr('D-infinity mass flux (SCIMAP default)'),
                tr('D8 mass flux (SCIMAP-Fitted research scripts)'),
            ],
            defaultValue=0))

        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_UPSLOPE_AREA_KM2,
            tr('River network threshold (km² upslope)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=0.8, minValue=0.0))

        self.add_advanced_parameter(QgsProcessingParameterNumber(
            self.MIN_RAINFALL_AREA_PERCENTILE,
            tr('Ignore cells below this percentile of contributing area'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=1.0, minValue=0.0, maxValue=100.0))

        self.addParameter(QgsProcessingParameterNumber(
            self.NO_REGRETS_TOP_PERCENT,
            tr('"No regrets" top tier (%)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=5.0, minValue=0.1, maxValue=100.0))

        self.add_advanced_parameter(QgsProcessingParameterNumber(
            self.MAX_WORKERS_ROUTING,
            tr('Concurrent flow-routing runs'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=2, minValue=1, maxValue=8))
        self.add_advanced_parameter(QgsProcessingParameterNumber(
            self.MAX_WORKERS_BLOCKS,
            tr('Concurrent row-block workers'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=4, minValue=1, maxValue=16))
        self.add_advanced_parameter(QgsProcessingParameterNumber(
            self.MEMORY_BUDGET_MB,
            tr('Memory budget for block processing (MB)'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=1500, minValue=256, maxValue=65536))
        self.add_advanced_parameter(QgsProcessingParameterBoolean(
            self.KEEP_INTERMEDIATE,
            tr('Keep the per-class routed rasters'), defaultValue=False))

        self.add_colour_ramp_parameter()
        self.add_wbt_parameter()

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_MEAN_STDEV, tr('Fitted risk: mean and standard deviation')))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_MEDIAN_IQR, tr('Fitted risk: median and IQR')))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_LOCAL_MEDIAN_IQR,
            tr('Local (unrouted) risk: median and IQR'),
            optional=True, createByDefault=False))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_NO_REGRETS_INCHANNEL,
            tr('No-regrets priority (in channel, %)')))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_NO_REGRETS_LANDSCAPE,
            tr('No-regrets priority (landscape, %)')))
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUT_NETWORK_POINTS, tr('River network risk points'),
            optional=True, createByDefault=False))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUT_RESULTS_CSV, tr('Per-combination network summary'),
            fileFilter='CSV files (*.csv)', optional=True, createByDefault=False))
        self.addParameter(QgsProcessingParameterFolderDestination(
            self.OUT_HISTOGRAMS, tr('Summary histograms folder'),
            optional=True, createByDefault=False))

    # ── Execution ───────────────────────────────────────────────────────

    def processAlgorithm(self, parameters, context, feedback):
        try:
            return self._run(parameters, context, feedback)
        except ScimapCanceled:
            feedback.pushInfo("SCIMAP Fitted ensemble mapping cancelled.")
            return {}

    def _run(self, parameters, context, feedback):
        weights_path = self.parameterAsFile(parameters, self.INPUT_WEIGHTS, context)
        n_weight_sets = self.parameterAsInt(parameters, self.N_WEIGHT_SETS, context)
        connectivity_layers = self.parameterAsLayerList(
            parameters, self.INPUT_CONNECTIVITY, context)
        land_cover_layers = self.parameterAsLayerList(parameters, self.INPUT_LC, context)
        erosion_layer = self.parameterAsRasterLayer(
            parameters, self.INPUT_EROSION, context)
        dem_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        routing_tool = _ROUTING_TOOLS[
            self.parameterAsEnum(parameters, self.ROUTING_TOOL, context)]
        min_area_km2 = self.parameterAsDouble(
            parameters, self.MIN_UPSLOPE_AREA_KM2, context)
        area_percentile = self.parameterAsDouble(
            parameters, self.MIN_RAINFALL_AREA_PERCENTILE, context)
        top_percent = self.parameterAsDouble(
            parameters, self.NO_REGRETS_TOP_PERCENT, context)
        routing_workers = max(1, self.parameterAsInt(
            parameters, self.MAX_WORKERS_ROUTING, context))
        block_workers = max(1, self.parameterAsInt(
            parameters, self.MAX_WORKERS_BLOCKS, context))
        budget_bytes = self.parameterAsInt(
            parameters, self.MEMORY_BUDGET_MB, context) * 1024 * 1024
        keep_intermediate = self.parameterAsBool(
            parameters, self.KEEP_INTERMEDIATE, context)
        self.resolve_wbt_executable(parameters, context, feedback)

        if not connectivity_layers or not land_cover_layers:
            raise RuntimeError(
                "At least one connectivity raster and one land cover raster are "
                "required.")

        remap, already_scimap = self.resolve_landcover_remap(
            parameters, context, feedback)

        weights, classes = fitted_io.read_weight_sets(weights_path, n_weight_sets)
        feedback.pushInfo(
            f"1. {weights.shape[0]} weight set(s) over {len(classes)} land-cover "
            f"class(es) from {os.path.basename(weights_path)}."
        )

        reference = gdal.Open(dem_layer.source())
        if reference is None:
            raise RuntimeError(f"Could not open the DEM: {dem_layer.source()}")
        width, height = reference.RasterXSize, reference.RasterYSize
        geotransform = reference.GetGeoTransform()
        cell_area = abs(geotransform[1] * geotransform[5])

        self._require_matching_grids(
            reference, connectivity_layers + land_cover_layers
            + ([erosion_layer] if erosion_layer else []), feedback)

        scratch = tempfile.mkdtemp(prefix='scimap_fitted_maps_')
        results = {}
        try:
            dem_path = self._routing_dem(parameters, context, feedback, dem_layer, scratch)
            rainfall_area = self._rainfall_area(
                parameters, context, feedback, width, height)
            erosion = self._erosion(erosion_layer, width, height, feedback)

            class_paths = self._route_per_class(
                parameters, context, feedback, scratch, dem_path, reference,
                connectivity_layers, land_cover_layers, classes, erosion,
                rainfall_area, remap, already_scimap, routing_tool,
                routing_workers, min_area_km2, cell_area, area_percentile)

            n_combinations = (len(connectivity_layers) * len(land_cover_layers)
                              * weights.shape[0])
            block_rows = ensemble.block_rows_for(
                width, n_combinations, budget_bytes, block_workers, height)
            feedback.pushInfo(
                f"4. Combining {n_combinations} combination(s) in blocks of "
                f"{block_rows} row(s)."
            )

            network = self._network_cells(
                parameters, context, feedback, width, height, cell_area,
                min_area_km2, reference)

            results.update(self._summarise(
                parameters, context, feedback, reference, width, height,
                class_paths, weights, classes, block_rows, block_workers,
                n_combinations, top_percent, network, scratch,
                connectivity_layers, land_cover_layers, geotransform))

            results.update(self._local_risk(
                parameters, context, feedback, reference, width, height,
                connectivity_layers, land_cover_layers, weights, classes,
                erosion, remap, already_scimap, block_rows, block_workers,
                top_percent, scratch))

            feedback.setProgress(100)
        finally:
            if keep_intermediate:
                feedback.pushInfo(f"Per-class routed rasters kept in {scratch}")
            else:
                shutil.rmtree(scratch, ignore_errors=True)
            reference = None

        self._style(parameters, context, results)
        return results

    # ── Inputs ──────────────────────────────────────────────────────────

    @staticmethod
    def _require_matching_grids(reference, layers, feedback):
        """Every input must already share one grid; nothing is resampled here.

        Resampling silently would change the calibrated quantity: the weights
        were fitted against statistics computed on a particular grid.
        """
        mismatched = []
        for layer in layers:
            dataset = gdal.Open(layer.source())
            if dataset is None:
                raise RuntimeError(f"Could not open raster: {layer.source()}")
            if not raster_io.grids_match(reference, dataset):
                mismatched.append(os.path.basename(layer.source()))
            dataset = None
        if mismatched:
            raise RuntimeError(
                "These rasters are not on the DEM's grid: "
                + ", ".join(mismatched)
                + ". Align them first — SCIMAP Fitted must not resample them, "
                "because the weights were calibrated against statistics computed "
                "on one grid."
            )

    def _routing_dem(self, parameters, context, feedback, dem_layer, scratch):
        if not self.parameterAsBool(parameters, self.BREACH_DEPRESSIONS, context):
            return dem_layer.source()
        feedback.pushInfo("Breaching DEM depressions before routing...")
        path = os.path.join(scratch, 'dem_breached.tif')
        self.run_wbt('BreachDepressions',
                     {'dem': dem_layer.source(), 'output': path}, feedback)
        return path

    def _rainfall_area(self, parameters, context, feedback, width, height):
        layer = self.parameterAsRasterLayer(
            parameters, self.INPUT_RAINFALL_AREA, context)
        if layer is None:
            feedback.pushWarning(
                "No rainfall-weighted contributing area supplied, so routed risk "
                "will not be diluted and will simply grow with catchment area. "
                "Supply the raster that 'SCIMAP Fitted 1: Catchment Statistics' "
                "writes."
            )
            return None
        array = self._read(layer.source())
        array = np.where(np.isfinite(array) & (array > 0), array, np.nan)

        percentile = self.parameterAsDouble(
            parameters, self.MIN_RAINFALL_AREA_PERCENTILE, context)
        if percentile > 0:
            finite = array[np.isfinite(array)]
            if finite.size:
                floor = float(np.percentile(finite, percentile))
                array = np.where(array >= floor, array, np.nan)
                feedback.pushInfo(
                    f"   Contributing-area floor at the {percentile:g}th "
                    f"percentile ({floor:.4g}); cells below it are excluded.")
        return array

    def _erosion(self, erosion_layer, width, height, feedback):
        if erosion_layer is None:
            feedback.pushInfo(
                "No erosion potential raster supplied; using a constant 1, so "
                "risk is connectivity times land-cover weight alone.")
            return np.ones((height, width), dtype=np.float32)

        raw = self._read(erosion_layer.source())
        low, high = percentile_range(raw, 5, 95, max_samples=_PERCENTILE_SAMPLE_CAP)
        if not np.isfinite(low) or high <= low:
            feedback.pushWarning(
                "Erosion potential has no usable range; using it unscaled.")
            return raw.astype(np.float32, copy=False)
        feedback.pushInfo(
            f"   Erosion normalised to 0-1 between {low:.4g} and {high:.4g} "
            "(5th/95th percentiles), matching the calibration stage.")
        return np.clip((raw - low) / (high - low), 0.0, 1.0).astype(np.float32, copy=False)

    # ── Routing ─────────────────────────────────────────────────────────

    def _route_per_class(self, parameters, context, feedback, scratch, dem_path,
                         reference, connectivity_layers, land_cover_layers,
                         classes, erosion, rainfall_area, remap, already_scimap,
                         routing_tool, workers, min_area_km2, cell_area,
                         area_percentile):
        """One routing run per (connectivity, land cover, class) — the trick.

        Routing once per class rather than once per weight set is what makes
        this affordable; see ``core/ensemble.py``.
        """
        jobs = []
        for conn_index, conn_layer in enumerate(connectivity_layers):
            connectivity = self._read(conn_layer.source())
            connectivity = np.where(connectivity < 0, np.nan, connectivity)
            for lc_index, lc_layer in enumerate(land_cover_layers):
                lc_classes = self.remap_to_scimap_classes(
                    self._read(lc_layer.source()), remap, already_scimap, feedback)
                for class_id in classes:
                    jobs.append((conn_index, lc_index, class_id,
                                 connectivity, lc_classes))

        total = len(jobs)
        feedback.setProgress(10)
        feedback.pushInfo(
            f"2. Routing {total} per-class loading(s) with {routing_tool} "
            f"({workers} at a time). This is the slow step."
        )

        efficiency, absorption = self._routing_constants(scratch, reference, erosion)
        paths = {}
        completed = 0
        started = time.time()

        def route(job):
            conn_index, lc_index, class_id, connectivity, lc_classes = job
            worker_feedback = CollectingFeedback()
            tag = f"c{conn_index}_l{lc_index}_k{class_id}"
            loading = np.where(
                np.isfinite(lc_classes) & (lc_classes == class_id),
                connectivity * erosion, 0.0)
            loading = np.where(np.isfinite(connectivity), loading, np.nan)

            loading_path = os.path.join(scratch, f'loading_{tag}.tif')
            routed_path = os.path.join(scratch, f'routed_{tag}.tif')
            raster_io.save_raster(loading, loading_path, reference,
                                  gdal.GDT_Float32, wbt_compatible=True)
            self.run_wbt(routing_tool, {
                'dem': dem_path,
                'loading': loading_path,
                'efficiency': efficiency,
                'absorption': absorption,
                'output': routed_path,
            }, worker_feedback)

            routed = self._read(routed_path)
            if rainfall_area is not None:
                with np.errstate(invalid='ignore', divide='ignore'):
                    routed = routed / rainfall_area
            normalised_path = os.path.join(scratch, f'normalised_{tag}.tif')
            raster_io.save_raster(routed, normalised_path, reference, gdal.GDT_Float32)
            os.remove(loading_path)
            os.remove(routed_path)
            return (conn_index, lc_index, class_id), normalised_path, worker_feedback

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(route, job) for job in jobs]
            for future in as_completed(futures):
                key, path, worker_feedback = future.result()
                worker_feedback.replay_into(feedback)
                paths[key] = path
                completed += 1
                self.check_canceled(feedback)
                feedback.setProgress(10 + 35 * completed / total)
                if completed == 1:
                    each = time.time() - started
                    feedback.pushInfo(
                        f"   First routing run took {each:.0f}s; "
                        f"~{each * total / workers / 60:.0f} min projected for all "
                        f"{total}."
                    )
        return paths

    def _routing_constants(self, scratch, reference, template):
        efficiency = os.path.join(scratch, 'efficiency_ones.tif')
        absorption = os.path.join(scratch, 'absorption_zero.tif')
        if not os.path.exists(efficiency):
            raster_io.save_raster(np.ones_like(template), efficiency, reference,
                                  gdal.GDT_Float32, wbt_compatible=True)
            raster_io.save_raster(np.zeros_like(template), absorption, reference,
                                  gdal.GDT_Float32, wbt_compatible=True)
        return efficiency, absorption

    # ── Combination and outputs ─────────────────────────────────────────

    def _network_cells(self, parameters, context, feedback, width, height,
                       cell_area, min_area_km2, reference):
        """Cells whose upslope area exceeds the river-network threshold."""
        layer = self.parameterAsRasterLayer(
            parameters, self.INPUT_FLOW_ACCUM, context)
        if layer is None:
            feedback.pushInfo(
                "No flow accumulation supplied, so no river-network outputs will "
                "be produced (the full-grid rasters are unaffected).")
            return None

        accumulation = self._read(layer.source())
        threshold = min_area_km2 * 1e6 / max(cell_area, 1e-9)
        mask = np.isfinite(accumulation) & (accumulation > threshold)
        rows, cols = np.nonzero(mask)
        feedback.pushInfo(
            f"   River network: {rows.size:,} cell(s) above {min_area_km2:g} km² "
            f"({threshold:.0f} cells).")
        return {'mask': mask, 'rows': rows, 'cols': cols}

    def _block_reader(self, class_paths, weights, classes, combinations, width):
        """Build the ensemble for a row block from the per-class rasters.

        Runs on worker threads, so every dataset is opened here rather than
        shared: a GDAL dataset handle is not thread-safe.
        """
        n_sets = weights.shape[0]

        def read_block(row_start, row_end):
            rows = row_end - row_start
            out = np.empty((len(combinations) * n_sets, rows, width), dtype=np.float32)
            for position, (conn_index, lc_index) in enumerate(combinations):
                stack = np.empty((len(classes), rows, width), dtype=np.float32)
                for depth, class_id in enumerate(classes):
                    dataset = gdal.Open(class_paths[(conn_index, lc_index, class_id)])
                    band = dataset.GetRasterBand(1)
                    block = band.ReadAsArray(0, row_start, width, rows)
                    nodata = band.GetNoDataValue()
                    dataset = None
                    block = block.astype(np.float32, copy=False)
                    if nodata is not None:
                        block = np.where(block == nodata, np.nan, block)
                    stack[depth] = block
                target = out[position * n_sets:(position + 1) * n_sets]
                np.matmul(weights.astype(np.float32, copy=False),
                          stack.reshape(len(classes), -1),
                          out=target.reshape(n_sets, -1))
            return out
        return read_block

    def _summarise(self, parameters, context, feedback, reference, width, height,
                   class_paths, weights, classes, block_rows, workers,
                   n_combinations, top_percent, network, scratch,
                   connectivity_layers, land_cover_layers, geotransform):
        combinations = [
            (conn_index, lc_index)
            for conn_index in range(len(connectivity_layers))
            for lc_index in range(len(land_cover_layers))
        ]
        read_block = self._block_reader(
            class_paths, weights, classes, combinations, width)

        mean_path = self.parameterAsOutputLayer(parameters, self.OUT_MEAN_STDEV, context)
        median_path = self.parameterAsOutputLayer(parameters, self.OUT_MEDIAN_IQR, context)

        mean_ds = raster_io.create_output_dataset(
            mean_path, width, height, 2, reference, gdal.GDT_Float32,
            band_names=['mean', 'stdev'])
        median_ds = raster_io.create_output_dataset(
            median_path, width, height, 2, reference, gdal.GDT_Float32,
            band_names=['median', 'iqr'])

        network_totals = {
            'sum': np.zeros(n_combinations, dtype=np.float64),
            'count': np.zeros(n_combinations, dtype=np.float64),
        }
        network_values = {}

        def reduce_block(stacked, row_start):
            if network is None:
                return None
            block_mask = network['mask'][row_start:row_start + stacked.shape[1]]
            if not block_mask.any():
                return None
            selected = stacked[:, block_mask]
            finite = np.isfinite(selected)
            return (
                np.where(finite, selected, 0.0).sum(axis=1),
                finite.sum(axis=1),
                row_start,
            )

        def on_reduced(partial):
            totals, counts, _row_start = partial
            network_totals['sum'] += totals
            network_totals['count'] += counts

        def writer(dataset, band_index):
            def write(block, row_start):
                dataset.GetRasterBand(band_index).WriteArray(
                    np.where(np.isfinite(block), block, raster_io.NODATA),
                    0, row_start)
            return write

        feedback.setProgress(48)
        peak = ensemble.combination_stats(
            read_block, height, width, n_combinations, block_rows,
            write_mean=writer(mean_ds, 1), write_stdev=writer(mean_ds, 2),
            write_median=writer(median_ds, 1), write_iqr=writer(median_ds, 2),
            workers=workers,
            progress=lambda fraction: feedback.setProgress(48 + 22 * fraction),
            cancel=lambda: self.check_canceled(feedback),
            reduce_block=reduce_block, on_reduced=on_reduced,
        )
        mean_ds.FlushCache()
        median_ds = None
        mean_ds = None
        feedback.pushInfo(f"   Peak ensemble mean risk: {peak:.6g}")

        results = {
            self.OUT_MEAN_STDEV: mean_path,
            self.OUT_MEDIAN_IQR: median_path,
        }

        feedback.setProgress(72)
        results.update(self._no_regrets(
            parameters, context, feedback, self.OUT_NO_REGRETS_INCHANNEL,
            read_block, reference, width, height, n_combinations, block_rows,
            top_percent, 'in-channel'))

        if network is not None:
            results.update(self._network_outputs(
                parameters, context, feedback, network, network_totals,
                mean_path, median_path, geotransform, reference,
                connectivity_layers, land_cover_layers, weights.shape[0],
                block_rows, width, height))
        return results

    def _no_regrets(self, parameters, context, feedback, identifier, read_block,
                    reference, width, height, n_combinations, block_rows,
                    top_percent, label):
        path = self.parameterAsOutputLayer(parameters, identifier, context)
        if not path:
            return {}

        feedback.pushInfo(f"5. Building the {label} no-regrets map...")
        thresholds = ensemble.no_regrets_thresholds(
            read_block, height, width, n_combinations, block_rows, top_percent,
            cancel=lambda: self.check_canceled(feedback))

        dataset = raster_io.create_output_dataset(
            path, width, height, 1, reference, gdal.GDT_Float32,
            band_names=[f'percent_top_{top_percent:g}'])

        def write(block, row_start):
            dataset.GetRasterBand(1).WriteArray(
                np.where(np.isfinite(block), block, raster_io.NODATA), 0, row_start)

        ensemble.no_regrets_percentage(
            read_block, height, width, n_combinations, block_rows, thresholds,
            write, cancel=lambda: self.check_canceled(feedback))
        dataset.FlushCache()
        dataset = None
        return {identifier: path}

    def _local_risk(self, parameters, context, feedback, reference, width, height,
                    connectivity_layers, land_cover_layers, weights, classes,
                    erosion, remap, already_scimap, block_rows, workers,
                    top_percent, scratch):
        """Median/IQR of risk *before* routing, plus its no-regrets map.

        The routed map answers "where does risk end up"; this one answers
        "where does it come from", which is where an intervention goes.
        """
        median_path = self.parameterAsOutputLayer(
            parameters, self.OUT_LOCAL_MEDIAN_IQR, context)
        landscape_path = self.parameterAsOutputLayer(
            parameters, self.OUT_NO_REGRETS_LANDSCAPE, context)
        if not median_path and not landscape_path:
            return {}

        feedback.setProgress(86)
        feedback.pushInfo("6. Building local (pre-accumulation) risk outputs...")

        lookup = ensemble.build_weight_lookup(weights, classes)
        sources = [
            (conn_layer.source(), lc_layer.source())
            for conn_layer in connectivity_layers
            for lc_layer in land_cover_layers
        ]
        n_combinations = len(sources) * weights.shape[0]
        n_sets = weights.shape[0]

        def read_block(row_start, row_end):
            rows = row_end - row_start
            out = np.empty((n_combinations, rows, width), dtype=np.float32)
            erosion_block = erosion[row_start:row_end]
            for position, (conn_source, lc_source) in enumerate(sources):
                connectivity = self._read_window(conn_source, row_start, rows, width)
                connectivity = np.where(connectivity < 0, np.nan, connectivity)
                lc_block = self.remap_to_scimap_classes(
                    self._read_window(lc_source, row_start, rows, width),
                    remap, already_scimap)
                indices = np.where(
                    np.isfinite(lc_block), lc_block, 0).astype(np.intp)
                indices = np.clip(indices, 0, lookup.shape[1] - 1)
                weighted = lookup[:, indices]
                out[position * n_sets:(position + 1) * n_sets] = (
                    weighted * (connectivity * erosion_block)[np.newaxis, ...])
            return out

        results = {}
        if median_path:
            dataset = raster_io.create_output_dataset(
                median_path, width, height, 2, reference, gdal.GDT_Float32,
                band_names=['median', 'iqr'])

            def write(band_index):
                def _write(block, row_start):
                    dataset.GetRasterBand(band_index).WriteArray(
                        np.where(np.isfinite(block), block, raster_io.NODATA),
                        0, row_start)
                return _write

            ensemble.combination_stats(
                read_block, height, width, n_combinations, block_rows,
                write_median=write(1), write_iqr=write(2), workers=workers,
                cancel=lambda: self.check_canceled(feedback))
            dataset.FlushCache()
            dataset = None
            results[self.OUT_LOCAL_MEDIAN_IQR] = median_path

        results.update(self._no_regrets(
            parameters, context, feedback, self.OUT_NO_REGRETS_LANDSCAPE,
            read_block, reference, width, height, n_combinations, block_rows,
            top_percent, 'landscape'))
        return results

    def _network_outputs(self, parameters, context, feedback, network,
                         network_totals, mean_path, median_path, geotransform,
                         reference, connectivity_layers, land_cover_layers,
                         n_weight_sets, block_rows, width, height):
        results = {}
        rows, cols = network['rows'], network['cols']

        points_path = self.parameterAsOutputLayer(
            parameters, self.OUT_NETWORK_POINTS, context)
        if points_path and rows.size:
            feedback.pushInfo("7. Sampling the river network...")
            samples = {}
            for name, path, band in (
                ('mean', mean_path, 1), ('stdev', mean_path, 2),
                ('median', median_path, 1), ('iqr', median_path, 2),
            ):
                samples[name] = self._sample_band(path, band, rows, cols)

            coordinates = [
                vectors.cell_centre(geotransform, int(row), int(col))
                for row, col in zip(rows, cols)
            ]
            records = [
                {
                    'mean': float(samples['mean'][index]),
                    'stdev': float(samples['stdev'][index]),
                    'median': float(samples['median'][index]),
                    'iqr': float(samples['iqr'][index]),
                }
                for index in range(rows.size)
            ]
            vectors.write_point_layer(
                coordinates, points_path, reference.GetProjection(), records,
                layer_name='network_risk')
            results[self.OUT_NETWORK_POINTS] = points_path

        csv_path = self.parameterAsFileOutput(
            parameters, self.OUT_RESULTS_CSV, context)
        if csv_path:
            self._write_combination_csv(
                csv_path, network_totals, connectivity_layers,
                land_cover_layers, n_weight_sets)
            results[self.OUT_RESULTS_CSV] = csv_path

        folder = self.parameterAsString(parameters, self.OUT_HISTOGRAMS, context)
        if folder and self._write_histograms(
                folder, network_totals, mean_path, rows, cols, feedback):
            results[self.OUT_HISTOGRAMS] = folder
        return results

    def _write_combination_csv(self, path, network_totals, connectivity_layers,
                               land_cover_layers, n_weight_sets):
        import csv

        means = np.divide(
            network_totals['sum'], network_totals['count'],
            out=np.full_like(network_totals['sum'], np.nan),
            where=network_totals['count'] > 0)

        with open(path, 'w', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            writer.writerow(['connectivity', 'land_cover', 'weight_set',
                             'mean_network_risk', 'n_network_cells'])
            index = 0
            for conn_layer in connectivity_layers:
                for lc_layer in land_cover_layers:
                    for weight_set in range(n_weight_sets):
                        writer.writerow([
                            os.path.basename(conn_layer.source()),
                            os.path.basename(lc_layer.source()),
                            weight_set,
                            means[index],
                            int(network_totals['count'][index]),
                        ])
                        index += 1
        return path

    def _write_histograms(self, folder, network_totals, mean_path, rows, cols,
                          feedback):
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            feedback.pushWarning(
                "matplotlib is not available in this QGIS Python environment, so "
                "the summary histograms were skipped.")
            return False

        os.makedirs(folder, exist_ok=True)
        means = np.divide(
            network_totals['sum'], network_totals['count'],
            out=np.full_like(network_totals['sum'], np.nan),
            where=network_totals['count'] > 0)
        plotting.save_histogram(
            means, os.path.join(folder, 'combination_mean_risk.png'),
            'Mean in-channel risk by combination', 'Mean network risk')
        if rows.size:
            plotting.save_histogram(
                self._sample_band(mean_path, 1, rows, cols),
                os.path.join(folder, 'network_mean_risk.png'),
                'Ensemble mean risk across the river network', 'Mean risk')
        return True

    # ── Raster helpers ──────────────────────────────────────────────────

    @staticmethod
    def _read(path):
        dataset = gdal.Open(path)
        if dataset is None:
            raise RuntimeError(f"Could not open raster: {path}")
        band = dataset.GetRasterBand(1)
        array = band.ReadAsArray().astype(np.float32, copy=False)
        nodata = band.GetNoDataValue()
        dataset = None
        if nodata is not None:
            array = np.where(array == nodata, np.nan, array)
        return array

    @staticmethod
    def _read_window(path, row_start, rows, width):
        dataset = gdal.Open(path)
        band = dataset.GetRasterBand(1)
        array = band.ReadAsArray(0, row_start, width, rows).astype(
            np.float32, copy=False)
        nodata = band.GetNoDataValue()
        dataset = None
        if nodata is not None:
            array = np.where(array == nodata, np.nan, array)
        return array

    @staticmethod
    def _sample_band(path, band_index, rows, cols):
        dataset = gdal.Open(path)
        band = dataset.GetRasterBand(band_index)
        array = band.ReadAsArray().astype(np.float32, copy=False)
        nodata = band.GetNoDataValue()
        dataset = None
        if nodata is not None:
            array = np.where(array == nodata, np.nan, array)
        return array[rows, cols]

    def _style(self, parameters, context, results):
        for identifier, ramp in (
            (self.OUT_MEAN_STDEV, RAMP_FITTED),
            (self.OUT_MEDIAN_IQR, RAMP_FITTED),
            (self.OUT_LOCAL_MEDIAN_IQR, RAMP_FITTED),
            (self.OUT_NO_REGRETS_INCHANNEL, RAMP_UNCERTAINTY),
            (self.OUT_NO_REGRETS_LANDSCAPE, RAMP_UNCERTAINTY),
        ):
            if identifier in results:
                styling.style_output(
                    context, results[identifier],
                    self.colour_ramp_name(parameters, context, ramp))
        if self.OUT_NETWORK_POINTS in results:
            styling.style_vector_output(
                context, results[self.OUT_NETWORK_POINTS], 'median',
                self.colour_ramp_name(parameters, context, RAMP_FITTED))
