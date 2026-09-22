"""SCIMAP Fitted 1: Catchment Statistics — Steps 1 and 2 of the research scripts.

Delineates the catchment upstream of every observation site and summarises
modelled risk inside it, per land-cover class, producing the design table the
calibration step searches against.

Steps 1 and 2 are one algorithm because they cannot usefully be separated in
QGIS: Step 2 needs exactly what Step 1 built, on the same grid, and the
scripts' handoff between them is a *file-naming* contract
(``catchment_12.shp`` means site 12) that Processing models cannot express.
Merged, it becomes one polygon layer carrying a real ``site_id`` field.

Two things here differ from the obvious reuse of ``ScimapRiskAlgorithm`` and
both matter:

* **Erosion is normalised before any weighting.** ``ScimapRiskAlgorithm``
  multiplies erosion by the land-cover weight and *then* takes its 5th/95th
  percentile stretch. Doing that here would make the stretch depend on the
  weights that are being solved for — circular. The weights enter later, in
  calibration, as the thing being fitted.
* **Pour points are snapped to the D8-derived stream raster.** ``Watershed``
  follows the D8 pointer, so a pour point sitting on a cell of the FD8-derived
  network whose D8 downstream neighbour is not in that network yields a tiny or
  empty basin.
"""

import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from osgeo import gdal, ogr
from qgis.core import (
    QgsProcessing,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterField,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorDestination,
    QgsProcessingParameterVectorLayer,
)

from ..core import fitted_io, styling, vectors, zonal
from ..data.defaults import (
    RAMP_CONNECTIVITY,
    RAMP_EROSION,
    RAMP_FITTED,
)
from ..localization import tr
from .base import ScimapCanceled
from .fitted_base import CollectingFeedback, ScimapFittedAlgorithmBase

#: Percentiles are estimated from at most this many cells; see
#: ``core/erosion.py::percentile_range``.
_PERCENTILE_SAMPLE_CAP = 5_000_000


class ScimapFittedStatsAlgorithm(ScimapFittedAlgorithmBase):
    """Per-site catchments and per-land-cover-class risk statistics."""

    INPUT_DEM = 'INPUT_DEM'
    INPUT_SITES = 'INPUT_SITES'
    SITE_ID_FIELD = 'SITE_ID_FIELD'
    OBS_FIELDS = 'OBS_FIELDS'
    INPUT_LC = 'INPUT_LC'
    INPUT_RAIN = 'INPUT_RAIN'
    INPUT_CONNECTIVITY = 'INPUT_CONNECTIVITY'
    INPUT_EROSION = 'INPUT_EROSION'
    INPUT_RAINFALL_AREA = 'INPUT_RAINFALL_AREA'

    EROSION_BASIS = 'EROSION_BASIS'
    USE_STREAM_POWER = 'USE_STREAM_POWER'
    STREAM_THRESHOLD = 'STREAM_THRESHOLD'
    SNAP_MODE = 'SNAP_MODE'
    SNAP_DISTANCE = 'SNAP_DISTANCE'
    STATISTICS = 'STATISTICS'
    MIN_PART_AREA = 'MIN_PART_AREA'
    MAX_WORKERS_WATERSHED = 'MAX_WORKERS_WATERSHED'

    OUT_STATS = 'OUT_STATS'
    OUT_CATCHMENTS = 'OUT_CATCHMENTS'
    OUT_SNAPPED = 'OUT_SNAPPED'
    OUT_CONNECTIVITY = 'OUT_CONNECTIVITY'
    OUT_EROSION = 'OUT_EROSION'
    OUT_RAINFALL_AREA = 'OUT_RAINFALL_AREA'
    OUT_DEM_BREACHED = 'OUT_DEM_BREACHED'
    OUT_FLOW_ACCUM = 'OUT_FLOW_ACCUM'

    _ICON = 'icon_fitted.svg'

    def createInstance(self):
        return ScimapFittedStatsAlgorithm()

    def name(self):
        return 'fittedstats'

    def displayName(self):
        return tr('SCIMAP Fitted 1: Catchment Statistics')

    def shortHelpString(self):
        return tr(
            'Delineates the catchment upstream of each water-quality monitoring '
            'site and summarises modelled risk inside it, per land-cover class. '
            'The resulting table is the input to "SCIMAP Fitted 2: Calibrate '
            'Weights".\n\n'
            'For every site and land-cover class it records the class area and '
            'the mean of connectivity x erosion, plus a per-site dilution '
            'factor — the rainfall-weighted contributing area at the outlet, '
            'which stands in for discharge. Further statistics (median, min, '
            'max, standard deviation, sum) can be summarised alongside the '
            'mean, and any of them chosen in step 2.\n\n'
            'Erosion is normalised to 0-1 by its 5th and 95th percentiles '
            'BEFORE any land-cover weighting, because the weighting is what the '
            'calibration step solves for.\n\n'
            'Observation sites are snapped to the nearest unoccupied stream '
            'cell. No two sites share a cell: two sites on one reach that '
            'collapsed onto the same cell would produce two identical '
            'catchments and silently inflate the fitted correlation.\n\n'
            'The optional raster outputs exist so "SCIMAP Fitted 3: Ensemble Risk '
            'Maps" can reuse this hydrology instead of recomputing it.'
        )

    # ── Parameters ──────────────────────────────────────────────────────

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_DEM, tr('Digital Elevation Model (DEM)')))

        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT_SITES, tr('Observation sites'),
            types=[QgsProcessing.TypeVectorPoint]))

        self.addParameter(QgsProcessingParameterField(
            self.SITE_ID_FIELD, tr('Site ID field'),
            parentLayerParameterName=self.INPUT_SITES, optional=True))

        self.addParameter(QgsProcessingParameterField(
            self.OBS_FIELDS,
            tr('Observed value field(s) — one per determinand'),
            parentLayerParameterName=self.INPUT_SITES,
            type=QgsProcessingParameterField.Numeric,
            allowMultiple=True, optional=True))

        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_LC, tr('Land Cover Map')))
        self.add_landcover_scheme_parameters()

        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_RAIN, tr('Rainfall Map'), optional=True))

        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_CONNECTIVITY,
            tr('Connectivity raster (optional; skips the connectivity solve)'),
            optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_EROSION,
            tr('Erosion potential raster (optional override)'), optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_RAINFALL_AREA,
            tr('Rainfall-weighted contributing area (optional override)'),
            optional=True))

        self.add_connectivity_method_parameter()

        self.addParameter(QgsProcessingParameterEnum(
            self.EROSION_BASIS,
            tr('Erosion potential basis'),
            options=[
                tr('Rainfall-weighted upslope area (SCIMAP-Fitted)'),
                tr('Upslope cell area (other SCIMAP tools)'),
            ],
            defaultValue=0))

        self.addParameter(QgsProcessingParameterBoolean(
            self.USE_STREAM_POWER,
            tr('Use stream power in erosion calculation'), defaultValue=True))

        self.addParameter(QgsProcessingParameterNumber(
            self.STREAM_THRESHOLD,
            tr('Stream Initiation Threshold (m²)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=800000.0, minValue=0.0))

        self.addParameter(QgsProcessingParameterEnum(
            self.SNAP_MODE,
            tr('Snap sites to'),
            options=[
                tr('Nearest stream cell (SCIMAP-Fitted)'),
                tr('Highest flow accumulation nearby (Delineate Catchment)'),
            ],
            defaultValue=0))

        self.addParameter(QgsProcessingParameterNumber(
            self.SNAP_DISTANCE,
            tr('Maximum snapping distance (map units)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=500.0, minValue=0.0))

        self.add_advanced_parameter(QgsProcessingParameterEnum(
            self.STATISTICS,
            tr('Per-class statistics to summarise'),
            options=list(zonal.SUPPORTED_STATISTICS),
            allowMultiple=True,
            defaultValue=[0],
        ))

        self.add_advanced_parameter(QgsProcessingParameterNumber(
            self.MIN_PART_AREA,
            tr('Drop catchment parts smaller than (map units²)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=0.0, minValue=0.0))

        self.add_advanced_parameter(QgsProcessingParameterNumber(
            self.MAX_WORKERS_WATERSHED,
            tr('Concurrent watershed delineations'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=2, minValue=1, maxValue=8))

        self.add_colour_ramp_parameter()
        self.add_wbt_parameter()

        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUT_STATS, tr('Catchment statistics table'),
            fileFilter='CSV files (*.csv)'))
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUT_CATCHMENTS, tr('Site catchments')))
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUT_SNAPPED, tr('Snapped observation sites'),
            optional=True, createByDefault=False))

        for identifier, label in (
            (self.OUT_CONNECTIVITY, 'Connectivity (for ensemble mapping)'),
            (self.OUT_EROSION, 'Erosion potential (for ensemble mapping)'),
            (self.OUT_RAINFALL_AREA, 'Rainfall-weighted area (for ensemble mapping)'),
            (self.OUT_DEM_BREACHED, 'Breached DEM (for ensemble mapping)'),
            (self.OUT_FLOW_ACCUM, 'D8 flow accumulation (for ensemble mapping)'),
        ):
            self.addParameter(QgsProcessingParameterRasterDestination(
                identifier, tr(label), optional=True, createByDefault=False))

    # ── Execution ───────────────────────────────────────────────────────

    def processAlgorithm(self, parameters, context, feedback):
        try:
            return self._run(parameters, context, feedback)
        except ScimapCanceled:
            feedback.pushInfo("SCIMAP Fitted catchment statistics cancelled.")
            return {}

    def _run(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        lc_layer = self.parameterAsRasterLayer(parameters, self.INPUT_LC, context)
        rain_layer = self.parameterAsRasterLayer(parameters, self.INPUT_RAIN, context)
        sites_layer = self.parameterAsVectorLayer(parameters, self.INPUT_SITES, context)

        use_stream_power = self.parameterAsBool(
            parameters, self.USE_STREAM_POWER, context)
        connectivity_method = self.connectivity_method_value(parameters, context)
        erosion_basis = self.parameterAsEnum(parameters, self.EROSION_BASIS, context)
        stream_m2 = self.parameterAsDouble(parameters, self.STREAM_THRESHOLD, context)
        snap_mode = self.parameterAsEnum(parameters, self.SNAP_MODE, context)
        snap_distance = self.parameterAsDouble(parameters, self.SNAP_DISTANCE, context)
        min_part_area = self.parameterAsDouble(parameters, self.MIN_PART_AREA, context)
        statistics = self._statistics(parameters, context)
        max_workers = max(1, self.parameterAsInt(
            parameters, self.MAX_WORKERS_WATERSHED, context))
        self.resolve_wbt_executable(parameters, context, feedback)

        id_field = (self.parameterAsFields(
            parameters, self.SITE_ID_FIELD, context) or [None])[0]
        obs_fields = self.parameterAsFields(parameters, self.OBS_FIELDS, context)

        remap, already_scimap = self.resolve_landcover_remap(
            parameters, context, feedback)

        results = {}
        with tempfile.TemporaryDirectory(prefix='scimap_fitted_') as tmpdir:
            dem_path = dem_layer.source()

            probe = gdal.Open(dem_path)
            if probe is None:
                raise RuntimeError(f"Could not open the DEM: {dem_path}")
            geotransform = probe.GetGeoTransform()
            probe = None
            cell_area = abs(geotransform[1] * geotransform[5])
            cell_size = abs(geotransform[1])
            stream_cells = max(1.0, stream_m2 / cell_area)

            hydro = self.run_hydrology(
                dem_path, tmpdir, feedback, stream_threshold=stream_cells)
            mask = hydro.mask_arr

            rain_scaled = self._rainfall(
                parameters, context, feedback, rain_layer, dem_path, tmpdir, mask)

            rainfall_area = self._rainfall_weighted_area(
                parameters, context, feedback, hydro, rain_scaled, dem_path, tmpdir)

            erosion = self._erosion(
                parameters, context, feedback, hydro, rainfall_area, cell_area,
                erosion_basis, use_stream_power, dem_path, tmpdir, mask)

            connectivity = self._connectivity(
                parameters, context, feedback, hydro, rain_scaled, dem_path,
                tmpdir, connectivity_method)

            self.check_canceled(feedback)
            feedback.setProgress(60)
            feedback.pushInfo("6. Combining connectivity x erosion...")
            product = (connectivity * erosion).astype(np.float32, copy=False)
            product[~mask] = np.nan

            lc_classes = self._land_cover(
                parameters, context, feedback, lc_layer, dem_path, tmpdir,
                remap, already_scimap, mask)

            # Neither is read again, and the per-site loop below allocates a
            # full-grid basin array per worker.
            hydro.dem_fill_arr = None
            hydro.slope_arr = None

            sites, snapped = self._snap_sites(
                sites_layer, dem_layer, context, feedback, hydro,
                geotransform, snap_mode, snap_distance, cell_size,
                id_field, obs_fields)

            rows, geometries, records = self._per_site_statistics(
                feedback, hydro, tmpdir, sites, snapped, lc_classes, product,
                rainfall_area, cell_area, min_part_area, max_workers, obs_fields,
                statistics)

            self.check_canceled(feedback)
            feedback.setProgress(88)
            feedback.pushInfo("8. Writing outputs...")

            stats_path = self.parameterAsFileOutput(parameters, self.OUT_STATS, context)
            fitted_io.write_stats_csv(stats_path, rows, obs_fields)
            results[self.OUT_STATS] = stats_path
            feedback.pushInfo(
                f"   {len(rows)} rows for {len(sites)} sites "
                f"x {len({row['land_cover_class'] for row in rows})} land-cover classes."
            )

            projection = hydro.slope_ds.GetProjection()
            catchments_path = self.parameterAsOutputLayer(
                parameters, self.OUT_CATCHMENTS, context)
            if catchments_path and geometries:
                vectors.write_polygon_layer(
                    geometries, catchments_path, projection, records,
                    layer_name='site_catchments')
                results[self.OUT_CATCHMENTS] = catchments_path

            snapped_path = self.parameterAsOutputLayer(
                parameters, self.OUT_SNAPPED, context)
            if snapped_path:
                vectors.write_point_layer(
                    [(item['x'], item['y']) for item in snapped],
                    snapped_path, projection,
                    [
                        {
                            'site_id': str(site['site_id']),
                            'moved_m': float(item['moved_cells'] * cell_size),
                            'fallback': int(bool(item['fallback'])),
                        }
                        for site, item in zip(sites, snapped)
                    ],
                    layer_name='snapped_sites')
                results[self.OUT_SNAPPED] = snapped_path

            results.update(self._write_rasters(
                parameters, context, feedback, hydro, connectivity, erosion,
                rainfall_area, mask))

            feedback.setProgress(100)

        self._style(parameters, context, results)
        return results

    # ── Pipeline pieces ─────────────────────────────────────────────────

    def _rainfall(self, parameters, context, feedback, rain_layer, dem_path,
                  tmpdir, mask):
        if rain_layer is None:
            feedback.pushInfo(
                "No rainfall map supplied; using uniform rainfall. The dilution "
                "factor then reduces to contributing area alone."
            )
            return np.ones(mask.shape, dtype=np.float32)

        rain_path = self.align_input(
            rain_layer, dem_path, tmpdir, 'rainfall_aligned.tif',
            feedback, 'Rainfall map', resample='bilinear')
        dataset = gdal.Open(rain_path)
        band = dataset.GetRasterBand(1)
        array = band.ReadAsArray().astype(np.float32, copy=False)
        nodata = band.GetNoDataValue()
        dataset = None
        if nodata is not None:
            array = np.where(array == nodata, np.nan, array)
        return self.scale_rainfall(array, mask)

    def _rainfall_weighted_area(self, parameters, context, feedback, hydro,
                                rain_scaled, dem_path, tmpdir):
        """The dilution denominator: rainfall routed downslope, as in Step 2."""
        override = self.parameterAsRasterLayer(
            parameters, self.INPUT_RAINFALL_AREA, context)
        if override is not None:
            feedback.pushInfo("Using the supplied rainfall-weighted area raster.")
            path = self.align_input(
                override, dem_path, tmpdir, 'rainfall_area_aligned.tif',
                feedback, 'Rainfall-weighted area', resample='bilinear')
            return self._read_masked(path, hydro.mask_arr)

        self.check_canceled(feedback)
        feedback.setProgress(30)
        feedback.pushInfo("4. Routing rainfall-weighted contributing area...")
        loading = np.where(
            np.isfinite(rain_scaled), rain_scaled * hydro.cell_area, 0.0)
        array, _path = self.route_mass_flux(
            hydro, loading, 'rainfall', feedback, slug='rainfall_area')
        if array is None:
            feedback.pushWarning(
                "Falling back to local contributing area for the dilution "
                "factor; sites will be diluted by area alone."
            )
            accum = np.where(hydro.mask_arr, np.abs(hydro.accum_arr), np.nan)
            return (accum * hydro.cell_area * rain_scaled).astype(np.float32, copy=False)
        return array

    def _erosion(self, parameters, context, feedback, hydro, rainfall_area,
                 cell_area, basis, use_stream_power, dem_path, tmpdir, mask):
        override = self.parameterAsRasterLayer(
            parameters, self.INPUT_EROSION, context)
        if override is not None:
            feedback.pushInfo("Using the supplied erosion potential raster.")
            path = self.align_input(
                override, dem_path, tmpdir, 'erosion_aligned.tif',
                feedback, 'Erosion potential', resample='bilinear')
            raw = self._read_masked(path, mask)
        else:
            self.check_canceled(feedback)
            feedback.setProgress(38)
            if basis == 0 and rainfall_area is not None:
                feedback.pushInfo(
                    "5. Computing erosion potential (rainfall-weighted upslope "
                    "area x tan slope)...")
                # compute_erosion_risk multiplies by cell_area internally, so
                # hand it the already-area-weighted raster divided back out.
                accum_source = rainfall_area / cell_area
            else:
                if basis == 0:
                    feedback.pushWarning(
                        "Rainfall-weighted area is unavailable; falling back to "
                        "upslope cell area for erosion potential."
                    )
                feedback.pushInfo(
                    "5. Computing erosion potential (upslope cell area x tan slope)...")
                accum_source = np.where(mask, hydro.accum_arr, np.nan)

            slope = np.where(mask, hydro.slope_arr, np.nan)
            raw = self.compute_erosion_risk(
                accum_source, slope, cell_area, use_stream_power=use_stream_power)

        # Deliberately NOT multiplied by a land-cover weight first: the
        # percentile stretch must not depend on the weights being calibrated.
        erosion = self.normalise_percentile(
            raw, 5, 95, max_samples=_PERCENTILE_SAMPLE_CAP)
        erosion[~mask] = np.nan
        return erosion.astype(np.float32, copy=False)

    def _connectivity(self, parameters, context, feedback, hydro, rain_scaled,
                      dem_path, tmpdir, method):
        override = self.parameterAsRasterLayer(
            parameters, self.INPUT_CONNECTIVITY, context)
        if override is not None:
            feedback.pushInfo("Using the supplied connectivity raster.")
            path = self.align_input(
                override, dem_path, tmpdir, 'connectivity_aligned.tif',
                feedback, 'Connectivity', resample='bilinear')
            array = self._read_masked(path, hydro.mask_arr)
            # Negative connectivity is meaningless and would flip the sign of a
            # class's contribution to the calibration.
            return np.where(array < 0, np.nan, array).astype(np.float32, copy=False)

        return self.connectivity_with_progress(
            hydro, rain_scaled, feedback, method=method)

    def _land_cover(self, parameters, context, feedback, lc_layer, dem_path,
                    tmpdir, remap, already_scimap, mask):
        self.check_canceled(feedback)
        feedback.pushInfo("Reading and reclassifying land cover...")
        path = self.align_input(
            lc_layer, dem_path, tmpdir, 'landcover_aligned.tif',
            feedback, 'Land cover map', resample='near')
        array = self._read_masked(path, mask)
        classes = self.remap_to_scimap_classes(
            array, remap, already_scimap, feedback)
        present = sorted({
            int(value) for value in np.unique(classes[np.isfinite(classes)])
            if value > 0
        })
        feedback.pushInfo(
            f"   SCIMAP classes present: "
            f"{', '.join(f'{c} ({name})' for c, name in zip(present, self.scimap_class_labels(present)))}"
        )
        return classes

    def _snap_sites(self, sites_layer, dem_layer, context, feedback, hydro,
                    geotransform, snap_mode, snap_distance, cell_size,
                    id_field, obs_fields):
        self.check_canceled(feedback)
        feedback.setProgress(64)
        sites = self.read_observation_sites(
            sites_layer, dem_layer.crs(), context,
            id_field=id_field, value_fields=obs_fields, feedback=feedback)
        feedback.pushInfo(f"7. Snapping {len(sites)} observation site(s) to the network...")

        # The D8-derived network, not the FD8 one: Watershed walks the D8
        # pointer, and a pour point on a cell whose D8 downstream neighbour is
        # off-network produces an empty basin.
        stream_array = self._read_array(hydro.stream_vector_path)

        max_cells = max(1, int(round(snap_distance / max(cell_size, 1e-9))))
        snapped = vectors.snap_points_to_stream_cells(
            stream_array, geotransform,
            [(site['x'], site['y']) for site in sites],
            max_distance_cells=max_cells)

        if snap_mode == 1:
            snapped = self._snap_to_max_accumulation(
                feedback, hydro, geotransform, sites, snapped, max_cells)

        far = [
            (site['site_id'], item['moved_cells'] * cell_size)
            for site, item in zip(sites, snapped)
            if item['fallback'] or item['moved_cells'] * cell_size > snap_distance / 2
        ]
        if far:
            feedback.pushWarning(
                f"   {len(far)} site(s) moved a long way to reach the stream "
                "network — check the stream initiation threshold: "
                + ", ".join(f"{site} ({distance:.0f} m)" for site, distance in far[:10])
            )
        return sites, snapped

    def _snap_to_max_accumulation(self, feedback, hydro, geotransform, sites,
                                  snapped, max_cells):
        """Highest-accumulation snapping, but still one cell per site.

        ``vectors.snap_pour_point_to_max_accumulation`` has no notion of the
        other sites, so two nearby points can land on the same cell. Keep its
        choice where it is free and fall back to the nearest-cell result
        otherwise, so the one-site-one-cell guarantee survives either mode.
        """
        accumulation = self._read_array(hydro.accum_path)
        taken = set()
        adjusted = []
        for site, item in zip(sites, snapped):
            row, col = item['row'], item['col']
            try:
                x, y, _value = vectors.snap_pour_point_to_max_accumulation(
                    hydro.accum_path, site['x'], site['y'],
                    max_cells, threshold=0.0)
                candidate_row, candidate_col = vectors.cell_of(
                    geotransform, x, y, shape=accumulation.shape)
            except Exception as exc:
                feedback.pushWarning(
                    f"   Could not snap site {site['site_id']} to maximum "
                    f"accumulation ({exc}); using the nearest stream cell.")
                candidate_row, candidate_col = row, col

            if (candidate_row, candidate_col) in taken:
                candidate_row, candidate_col = row, col
            if (candidate_row, candidate_col) in taken:
                adjusted.append(item)
                taken.add((item['row'], item['col']))
                continue

            taken.add((candidate_row, candidate_col))
            snapped_x, snapped_y = vectors.cell_centre(
                geotransform, candidate_row, candidate_col)
            adjusted.append({
                **item,
                'row': candidate_row,
                'col': candidate_col,
                'x': snapped_x,
                'y': snapped_y,
            })
        return adjusted

    def _statistics(self, parameters, context):
        """Which per-class statistics to summarise.

        The mean is what the calibration reads by default; the others exist so
        a run can be calibrated against, say, the median instead, which is what
        the research scripts' ``--stats`` flag offers.
        """
        selected = self.parameterAsEnums(parameters, self.STATISTICS, context)
        names = [zonal.SUPPORTED_STATISTICS[index] for index in selected
                 if 0 <= index < len(zonal.SUPPORTED_STATISTICS)]
        if 'mean' not in names:
            # Always present: it is the alias every downstream default reads.
            names.insert(0, 'mean')
        return names

    def _per_site_statistics(self, feedback, hydro, tmpdir, sites, snapped,
                             lc_classes, product, rainfall_area, cell_area,
                             min_part_area, max_workers, obs_fields,
                             statistics=('mean',)):
        feedback.setProgress(68)
        feedback.pushInfo(
            f"   Delineating {len(sites)} catchment(s) "
            f"({max_workers} at a time)...")

        dilution = zonal.sample_array_at_cells(
            rainfall_area if rainfall_area is not None else np.ones(product.shape),
            [item['row'] for item in snapped],
            [item['col'] for item in snapped],
        )

        rows, geometries, records = [], [], []
        completed = 0
        projection = hydro.slope_ds.GetProjection()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self._delineate, tmpdir, hydro, index, item, min_part_area,
                ): index
                for index, item in enumerate(snapped)
            }
            for future in as_completed(futures):
                index = futures[future]
                basin_path, geometry_path, worker_feedback = future.result()
                # Back on the calling thread: only here is it safe to talk to
                # the real feedback object.
                worker_feedback.replay_into(feedback)
                self.check_canceled(feedback)

                site = sites[index]
                class_rows = self._summarise_basin(
                    basin_path, lc_classes, product, hydro.mask_arr, cell_area,
                    statistics)
                if not class_rows:
                    feedback.pushWarning(
                        f"   Site {site['site_id']} has an empty catchment and was "
                        "skipped. It is probably snapped onto a network cell with "
                        "no upslope area."
                    )
                    completed += 1
                    continue

                for entry in class_rows:
                    row = {
                        'site_id': site['site_id'],
                        'rainfall_dilution': float(dilution[index]),
                        **entry,
                    }
                    for field in obs_fields:
                        row[f"{fitted_io.OBS_PREFIX}{field}"] = site['values'].get(field)
                    rows.append(row)

                geometry = self._read_geometry(geometry_path)
                if geometry is not None:
                    geometries.append(geometry)
                    record = {
                        'site_id': str(site['site_id']),
                        'area_m2': float(sum(e['area_m2'] for e in class_rows)),
                        'rain_dil': float(dilution[index]),
                    }
                    for field in obs_fields:
                        # Not truncated here: write_feature_layer does that,
                        # and refuses a collision. Pre-truncating would hide
                        # two determinands silently overwriting each other.
                        record[f"obs_{field}"] = float(
                            site['values'].get(field, float('nan')))
                    records.append(record)

                completed += 1
                feedback.setProgress(68 + 20 * completed / max(1, len(snapped)))

        rows.sort(key=lambda row: (str(row['site_id']), row['land_cover_class']))
        return rows, geometries, records

    def _delineate(self, tmpdir, hydro, index, item, min_part_area):
        """Run Watershed for one site. Executes on a worker thread."""
        worker_feedback = CollectingFeedback()
        pour_path = os.path.join(tmpdir, f'pour_{index}.tif')
        basin_path = os.path.join(tmpdir, f'basin_{index}.tif')
        geometry_path = os.path.join(tmpdir, f'basin_{index}.gpkg')

        vectors.create_pour_point_raster(
            item['x'], item['y'], hydro.dem_fill_path, pour_path)
        self.run_wbt('Watershed', {
            'd8_pntr': hydro.d8_path,
            'pour_pts': pour_path,
            'output': basin_path,
        }, worker_feedback)

        try:
            vectors.vectorize_basin(
                basin_path, geometry_path, min_area=min_part_area,
                output_format='GPKG', layer_name='catchment')
        except Exception as exc:
            worker_feedback.pushWarning(
                f"Could not polygonise the catchment for site {index}: {exc}")
            geometry_path = None

        return basin_path, geometry_path, worker_feedback

    def _summarise_basin(self, basin_path, lc_classes, product, mask, cell_area,
                         statistics=('mean',)):
        basin = self._read_array(basin_path)
        if basin is None:
            return []
        window = zonal.basin_window(basin)
        if window is None:
            return []

        row0, row1, col0, col1 = window
        inside = (basin[row0:row1, col0:col1] > 0) & mask[row0:row1, col0:col1]
        return zonal.class_statistics(
            lc_classes[row0:row1, col0:col1],
            product[row0:row1, col0:col1],
            inside,
            cell_area,
            statistics,
        )

    # ── Raster/vector helpers ───────────────────────────────────────────

    @staticmethod
    def _read_array(path):
        dataset = gdal.Open(path)
        if dataset is None:
            return None
        band = dataset.GetRasterBand(1)
        array = band.ReadAsArray().astype(np.float32, copy=False)
        nodata = band.GetNoDataValue()
        dataset = None
        if nodata is not None:
            array = np.where(array == nodata, np.nan, array)
        return array

    def _read_masked(self, path, mask):
        array = self._read_array(path)
        if array is None:
            raise RuntimeError(f"Could not read raster: {path}")
        return np.where(mask, array, np.nan).astype(np.float32, copy=False)

    @staticmethod
    def _read_geometry(path):
        if not path or not os.path.exists(path):
            return None
        source = ogr.Open(path)
        if source is None:
            return None
        layer = source.GetLayer(0)
        feature = layer.GetNextFeature()
        geometry = feature.GetGeometryRef().Clone() if feature else None
        source = None
        return geometry

    def _write_rasters(self, parameters, context, feedback, hydro, connectivity,
                       erosion, rainfall_area, mask):
        """Persist the hydrology so the ensemble stage need not recompute it."""
        written = {}
        wanted = (
            (self.OUT_CONNECTIVITY, connectivity),
            (self.OUT_EROSION, erosion),
            (self.OUT_RAINFALL_AREA, rainfall_area),
        )
        for identifier, array in wanted:
            path = self.parameterAsOutputLayer(parameters, identifier, context)
            if path and array is not None:
                self.save_raster(array, path, hydro.slope_ds,
                                 gdal.GDT_Float32, mask)
                written[identifier] = path

        for identifier, source in (
            (self.OUT_DEM_BREACHED, hydro.dem_fill_path),
            (self.OUT_FLOW_ACCUM, hydro.d8_accum_path),
        ):
            path = self.parameterAsOutputLayer(parameters, identifier, context)
            if path and source and os.path.exists(source):
                array = self._read_array(source)
                self.save_raster(array, path, hydro.slope_ds, gdal.GDT_Float32)
                written[identifier] = path

        if written:
            feedback.pushInfo(
                "   Hydrology rasters written; pass them to 'SCIMAP Fitted 3: "
                "Ensemble Risk Maps' to skip recomputing them."
            )
        return written

    def _style(self, parameters, context, results):
        if self.OUT_EROSION in results:
            styling.style_output(
                context, results[self.OUT_EROSION],
                self.colour_ramp_name(parameters, context, RAMP_EROSION))
        if self.OUT_CONNECTIVITY in results:
            styling.style_output(
                context, results[self.OUT_CONNECTIVITY],
                self.colour_ramp_name(parameters, context, RAMP_CONNECTIVITY))
        if self.OUT_RAINFALL_AREA in results:
            styling.style_output(
                context, results[self.OUT_RAINFALL_AREA],
                self.colour_ramp_name(parameters, context, RAMP_FITTED))
