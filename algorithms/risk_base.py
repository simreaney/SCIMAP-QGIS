"""Shared pipeline for the SCIMAP risk-mapping tools.

SCIMAP Sediment and SCIMAP FIO run the identical hydrology, connectivity and
in-channel combination; they differ only in how the per-cell *risk weight* is
derived — land-cover risk weights for Sediment, FIO concentration for FIO.
Subclasses supply that one step through :meth:`build_risk_weight`.
"""

import os
import tempfile

import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsProcessingParameterBoolean,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorDestination,
)

from ..core import styling, vectors
from ..data.defaults import (
    RAMP_CONNECTIVITY,
    RAMP_EROSION,
    RAMP_SCIMAP,
)
from ..localization import tr
from .base import ScimapAlgorithmBase, ScimapCanceled


class ScimapRiskAlgorithm(ScimapAlgorithmBase):
    """Base for the DEM + rainfall + risk-weight SCIMAP risk maps."""

    INPUT_DEM = 'INPUT_DEM'
    INPUT_RAIN = 'INPUT_RAIN'
    STREAM_THRESHOLD = 'STREAM_THRESHOLD'
    USE_STREAM_POWER = 'USE_STREAM_POWER'

    OUT_CONNECTIVITY = 'OUT_CONNECTIVITY'
    OUT_EROSION = 'OUT_EROSION'
    OUT_VECTOR_STREAM = 'OUT_VECTOR_STREAM'
    OUT_STREAM_RISK_POINTS = 'OUT_STREAM_RISK_POINTS'
    OUT_KML = 'OUT_KML'

    #: Label used for the erosion output; FIO overrides it.
    EROSION_LABEL = 'Erosion Risk'

    # ── Parameters ──────────────────────────────────────────────────────

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_DEM, tr('Digital Elevation Model (DEM)')))

        self.add_risk_weight_parameters()

        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_RAIN, tr('Rainfall Map')))

        self.addParameter(QgsProcessingParameterNumber(
            self.STREAM_THRESHOLD,
            tr('Stream Initiation Threshold (m²)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=800000.0,
            minValue=0.0))

        self.addParameter(QgsProcessingParameterBoolean(
            self.USE_STREAM_POWER,
            tr('Use stream power in erosion calculation'),
            defaultValue=True,
            optional=False,
        ))

        self.add_connectivity_method_parameter()
        self.add_colour_ramp_parameter()
        self.add_wbt_parameter()

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_CONNECTIVITY, tr('Network Connectivity Risk')))

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_EROSION, tr(self.EROSION_LABEL)))

        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUT_VECTOR_STREAM, tr('Instream Risk Concentration')))

        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUT_STREAM_RISK_POINTS, tr('Stream Risk Points'),
            optional=True, createByDefault=False))

        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUT_KML, tr('Stream Network (KML)'),
            optional=True, createByDefault=False))

        self.add_extra_output_parameters()

    # ── Hooks for subclasses ────────────────────────────────────────────

    def add_risk_weight_parameters(self):
        """Add whatever inputs the subclass needs to derive its risk weight."""
        raise NotImplementedError

    def add_extra_output_parameters(self):
        """Optional extra destinations; overridden where needed."""

    def build_risk_weight(self, parameters, context, feedback, dem_path, tmpdir,
                          hydro):
        """Return the per-cell risk weight array (unmasked, DEM-aligned)."""
        raise NotImplementedError

    def write_extra_outputs(self, parameters, context, feedback, hydro, tmpdir):
        """Write any subclass-specific outputs; returns a dict of results."""
        return {}

    # ── Pipeline ────────────────────────────────────────────────────────

    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        rain_layer = self.parameterAsRasterLayer(parameters, self.INPUT_RAIN, context)
        stream_m2_threshold = self.parameterAsDouble(parameters, self.STREAM_THRESHOLD, context)
        use_stream_power = self.parameterAsBool(parameters, self.USE_STREAM_POWER, context)
        connectivity_method = self.connectivity_method_value(parameters, context)
        # Always route with WhiteboxTools DInfMassFlux, matching the SCIMAP web
        # application; the local-scaling shortcut this used to fall back to by
        # default has been retired.
        routing = 'massflux'
        self.resolve_wbt_executable(parameters, context, feedback)

        out_conn_path = self.parameterAsOutputLayer(parameters, self.OUT_CONNECTIVITY, context)
        out_eros_path = self.parameterAsOutputLayer(parameters, self.OUT_EROSION, context)
        out_vector_path = self.parameterAsOutputLayer(parameters, self.OUT_VECTOR_STREAM, context)
        out_points_path = self.parameterAsOutputLayer(parameters, self.OUT_STREAM_RISK_POINTS, context)
        out_kml_path = self.parameterAsOutputLayer(parameters, self.OUT_KML, context)

        # Prevent accidental output aliasing in the UI where erosion and
        # connectivity are pointed at the same file.
        raster_output_paths = [
            path for path in [out_conn_path, out_eros_path]
            if path
        ]
        if len(raster_output_paths) != len(set(raster_output_paths)):
            raise RuntimeError("Raster outputs must use different file paths.")

        results = {}
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dem_path = dem_layer.source()

                # Cell size is needed to convert the user's threshold (m²) into
                # cells before hydrology runs, so ExtractStreams actually uses
                # it rather than the fixed STREAM_VECTOR_THRESHOLD fallback.
                probe_ds = gdal.Open(dem_path)
                probe_gt = probe_ds.GetGeoTransform()
                probe_ds = None
                cell_area = abs(probe_gt[1] * probe_gt[5])
                stream_cell_threshold = max(1.0, stream_m2_threshold / cell_area)

                hydro = self.run_hydrology(
                    dem_path, tmpdir, feedback, stream_threshold=stream_cell_threshold,
                )
                mask_arr = hydro.mask_arr

                rain_path = self.align_input(
                    rain_layer, dem_path, tmpdir, 'rainfall_aligned.tif',
                    feedback, 'Rainfall map', resample='bilinear',
                )
                rain_ds = gdal.Open(rain_path)
                rain_arr = rain_ds.GetRasterBand(1).ReadAsArray().astype(np.float32, copy=False)
                rain_nodata = rain_ds.GetRasterBand(1).GetNoDataValue()
                rain_ds = None
                if rain_nodata is not None:
                    rain_arr = np.where(rain_arr == rain_nodata, np.nan, rain_arr)

                # Normalise rainfall by its in-catchment mean.
                rain_scaled = self.scale_rainfall(rain_arr, mask_arr)

                self.check_canceled(feedback)
                risk_weight = self.build_risk_weight(
                    parameters, context, feedback, dem_path, tmpdir, hydro,
                )

                feedback.setProgress(34)
                erosion_mode = "stream power" if use_stream_power else "upslope area only"
                feedback.pushInfo(f"4. Computing {self.EROSION_LABEL} ({erosion_mode})...")
                slope_arr = np.where(mask_arr, hydro.slope_arr, np.nan)
                accum_arr_masked = np.where(mask_arr, hydro.accum_arr, np.nan)
                hydro.slope_arr = slope_arr

                eros_raw = self.compute_erosion_risk(
                    accum_arr_masked,
                    slope_arr,
                    hydro.cell_area,
                    use_stream_power=use_stream_power,
                )
                eros_raw = eros_raw * risk_weight
                erosion_risk = self.normalise_percentile(eros_raw, 5, 95)
                erosion_risk[~mask_arr] = np.nan

                connectivity = self.connectivity_with_progress(
                    hydro, rain_scaled, feedback, method=connectivity_method,
                )
                # Neither is read again below (combine_scimap_output only
                # needs hydro.accum_arr, hydro.mask_arr and hydro.channel_mask);
                # drop them now so the peak-memory combination step that
                # follows isn't carrying dead weight alongside its own
                # full-raster temporaries.
                hydro.dem_fill_arr = None
                hydro.slope_arr = None

                # ``risk_concentration`` is the accumulated risk (erosion x
                # connectivity, routed across the whole catchment) divided by
                # the rainfall-weighted catchment area, valid everywhere in the
                # catchment; ``channel_risk_concentration`` is the same ratio
                # restricted to cells at or above the stream initiation
                # threshold, for the stream-risk-points export.
                risk_concentration, channel_risk_concentration = self.combine_scimap_output(
                    erosion_risk, connectivity, hydro, rain_scaled,
                    stream_cell_threshold, feedback, routing=routing,
                )

                self.check_canceled(feedback)
                feedback.setProgress(70)
                feedback.pushInfo("7. Writing output rasters...")
                self.save_raster(erosion_risk, out_eros_path, hydro.slope_ds,
                                 gdal.GDT_Float32, mask_arr)
                self.save_raster(connectivity, out_conn_path, hydro.slope_ds,
                                 gdal.GDT_Float32, mask_arr)

                results.update(self.write_extra_outputs(
                    parameters, context, feedback, hydro, tmpdir,
                ))

                self.check_canceled(feedback)
                feedback.setProgress(82)
                feedback.pushInfo(
                    "8. Generating Instream Risk Concentration network "
                    f"(Threshold: {int(hydro.channel_mask.sum())} channel cells)..."
                )
                temp_vector_path = os.path.join(tmpdir, "stream_vector.shp")
                self.run_wbt("RasterStreamsToVector", {
                    'streams': hydro.stream_vector_path,
                    'd8_pntr': hydro.d8_path,
                    'output': temp_vector_path,
                }, feedback)

                feedback.setProgress(90)
                if os.path.exists(temp_vector_path):
                    try:
                        # Sample the un-gated ratio so every reach the vector
                        # network actually covers gets a value, rather than only
                        # the (much coarser) reaches above the stream initiation
                        # threshold.
                        vectors.attribute_stream_network(
                            temp_vector_path, risk_concentration,
                            hydro.slope_ds.GetGeoTransform(), field_name="Risk",
                        )
                    except Exception as exc:
                        feedback.pushInfo(f"Could not calculate Risk values for streams: {exc}")

                    feedback.setProgress(95)
                    # WhiteboxTools writes the network without a CRS; stamp the
                    # DEM's projection on so the export and KML reprojection work.
                    _write_prj(temp_vector_path, hydro.slope_ds.GetProjection())
                    vectors.copy_vector(temp_vector_path, out_vector_path)

                    if out_kml_path:
                        feedback.pushInfo("9. Exporting stream network to KML...")
                        vectors.export_vector_kml(
                            out_vector_path, out_kml_path,
                            source_wkt=hydro.slope_ds.GetProjection(),
                        )
                        results[self.OUT_KML] = out_kml_path

                if out_points_path:
                    feedback.pushInfo("10. Exporting stream risk points...")
                    channel_risk_path = os.path.join(tmpdir, "channel_risk_concentration.tif")
                    self.save_raster(channel_risk_concentration, channel_risk_path,
                                     hydro.slope_ds, gdal.GDT_Float32, mask_arr)
                    n_points = vectors.raster_to_point_vector(
                        channel_risk_path, out_points_path, field_name="scimap_risk",
                    )
                    feedback.pushInfo(f"Wrote {n_points} stream risk points.")
                    results[self.OUT_STREAM_RISK_POINTS] = out_points_path

                feedback.setProgress(100)
        except ScimapCanceled:
            feedback.pushInfo("SCIMAP run cancelled.")
            return {}

        self.style_outputs(parameters, context, out_eros_path, out_conn_path,
                           out_vector_path, out_points_path)

        results.update({
            self.OUT_CONNECTIVITY: out_conn_path,
            self.OUT_EROSION: out_eros_path,
            self.OUT_VECTOR_STREAM: out_vector_path,
        })
        return results

    def style_outputs(self, parameters, context, eros, conn, streams, points):
        styling.style_output(
            context, eros, self.colour_ramp_name(parameters, context, RAMP_EROSION))
        styling.style_output(
            context, conn, self.colour_ramp_name(parameters, context, RAMP_CONNECTIVITY))
        styling.style_vector_output(
            context, streams, 'Risk',
            self.colour_ramp_name(parameters, context, RAMP_SCIMAP))
        if points:
            styling.style_vector_output(
                context, points, 'scimap_ris',
                self.colour_ramp_name(parameters, context, RAMP_SCIMAP))


def _write_prj(shapefile_path, projection_wkt):
    """Write a .prj sidecar for a shapefile that WhiteboxTools left unprojected."""
    if not projection_wkt:
        return
    prj_path = os.path.splitext(shapefile_path)[0] + '.prj'
    if os.path.exists(prj_path):
        return
    try:
        with open(prj_path, 'w', encoding='utf-8') as handle:
            handle.write(projection_wkt)
    except OSError:
        pass
