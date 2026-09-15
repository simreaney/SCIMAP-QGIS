"""Catchment delineation from a pour point.

Ported from ``processing/catchment.py`` in the SCIMAP web application, minus the
DEM-coverage validation, PostGIS storage and multi-dataset fallback machinery,
none of which applies to a user-supplied DEM in QGIS.

Clicking beside a channel rather than on it is the normal case, so the point is
snapped onto the nearest cell carrying a substantial flow accumulation before
the watershed is delineated.
"""

import os
import tempfile

from osgeo import gdal
from qgis.core import (
    QgsCoordinateTransform,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorDestination,
    QgsProject,
)

from ..core import vectors
from ..localization import tr
from .base import ScimapAlgorithmBase, ScimapCanceled


class ScimapCatchmentAlgorithm(ScimapAlgorithmBase):
    """Delineate the catchment upstream of a pour point."""

    INPUT_DEM = 'INPUT_DEM'
    POUR_POINT = 'POUR_POINT'
    SNAP_RADIUS = 'SNAP_RADIUS'
    SNAP_AREA_THRESHOLD = 'SNAP_AREA_THRESHOLD'
    SNAP_MIN_RATIO = 'SNAP_MIN_RATIO'
    MIN_PART_AREA = 'MIN_PART_AREA'
    BREACH_DEPRESSIONS = 'BREACH_DEPRESSIONS'

    OUT_CATCHMENT = 'OUT_CATCHMENT'
    OUT_POUR_POINT = 'OUT_POUR_POINT'
    OUT_BASIN_RASTER = 'OUT_BASIN_RASTER'

    _ICON = 'icon_catchment.svg'

    def createInstance(self):
        return ScimapCatchmentAlgorithm()

    def name(self):
        return 'scimapcatchment'

    def displayName(self):
        return tr('Delineate Catchment')

    def groupId(self):
        return 'scimap_catchment'

    def group(self):
        return tr('Catchment')

    def shortHelpString(self):
        return tr(
            'Delineates the catchment draining to a pour point.\n\n'
            'The point is snapped to the nearest cell whose upslope contributing '
            'area exceeds the snap threshold, so a click near — but not exactly '
            'on — the channel still returns the intended catchment. Use the map '
            'button beside the pour point to pick it from the canvas.'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_DEM, tr('Digital Elevation Model (DEM)')))

        self.addParameter(QgsProcessingParameterPoint(
            self.POUR_POINT, tr('Pour point (catchment outlet)')))

        self.addParameter(QgsProcessingParameterNumber(
            self.SNAP_RADIUS,
            tr('Snap search radius (map units)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=250.0,
            minValue=0.0))

        self.addParameter(QgsProcessingParameterNumber(
            self.SNAP_AREA_THRESHOLD,
            tr('Minimum contributing area to snap onto (m²)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=800000.0,
            minValue=0.0))

        self.addParameter(QgsProcessingParameterNumber(
            self.SNAP_MIN_RATIO,
            tr('Minimum accumulation ratio vs the clicked cell'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=100.0,
            minValue=1.0))

        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_PART_AREA,
            tr('Drop catchment parts smaller than (m²)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=0.0,
            minValue=0.0))

        self.addParameter(QgsProcessingParameterBoolean(
            self.BREACH_DEPRESSIONS,
            tr('Breach depressions before routing'),
            defaultValue=True))

        self.add_wbt_parameter()

        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUT_CATCHMENT, tr('Catchment Boundary')))

        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUT_POUR_POINT, tr('Snapped Pour Point'),
            optional=True, createByDefault=False))

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_BASIN_RASTER, tr('Basin Raster'),
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        snap_radius = self.parameterAsDouble(parameters, self.SNAP_RADIUS, context)
        snap_threshold = self.parameterAsDouble(parameters, self.SNAP_AREA_THRESHOLD, context)
        snap_ratio = self.parameterAsDouble(parameters, self.SNAP_MIN_RATIO, context)
        min_part_area = self.parameterAsDouble(parameters, self.MIN_PART_AREA, context)
        breach = self.parameterAsBool(parameters, self.BREACH_DEPRESSIONS, context)
        self.resolve_wbt_executable(parameters, context, feedback)

        # The point parameter carries its own CRS; move it onto the DEM's.
        point = self.parameterAsPoint(parameters, self.POUR_POINT, context, dem_layer.crs())

        out_catchment = self.parameterAsOutputLayer(parameters, self.OUT_CATCHMENT, context)
        out_pour_point = self.parameterAsOutputLayer(parameters, self.OUT_POUR_POINT, context)
        out_basin = self.parameterAsOutputLayer(parameters, self.OUT_BASIN_RASTER, context)

        results = {}
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dem_path = dem_layer.source()

                feedback.setProgress(2)
                feedback.pushInfo(f"Pour point: {point.x():.2f}, {point.y():.2f} ({dem_layer.crs().authid()})")

                dem_fill_path = dem_path
                if breach:
                    feedback.setProgress(5)
                    feedback.pushInfo("1. Filling DEM depressions...")
                    dem_fill_path = os.path.join(tmpdir, "dem_fill.tif")
                    self.run_wbt("BreachDepressions", {
                        'dem': dem_path,
                        'output': dem_fill_path,
                    }, feedback)

                self.check_canceled(feedback)
                feedback.setProgress(25)
                feedback.pushInfo("2. Computing D8 pointer and flow accumulation...")
                d8_path = os.path.join(tmpdir, "d8.tif")
                self.run_wbt("D8Pointer", {
                    'dem': dem_fill_path,
                    'output': d8_path,
                    'esri_pntr': False,
                }, feedback)

                accum_path = os.path.join(tmpdir, "accum.tif")
                self.run_wbt("D8FlowAccumulation", {
                    'i': dem_fill_path,
                    'out_type': 'cells',
                    'output': accum_path,
                }, feedback)

                self.check_canceled(feedback)
                feedback.setProgress(55)
                feedback.pushInfo("3. Snapping the pour point to the channel network...")
                accum_ds = gdal.Open(accum_path)
                gt = accum_ds.GetGeoTransform()
                cell_area = abs(gt[1] * gt[5])
                accum_ds = None

                snap_cells = snap_threshold / cell_area if cell_area else snap_threshold
                snap_x, snap_y, snapped_accum = vectors.snap_pour_point_to_max_accumulation(
                    accum_path, point.x(), point.y(), snap_radius, snap_cells, snap_ratio,
                )
                moved = ((snap_x - point.x()) ** 2 + (snap_y - point.y()) ** 2) ** 0.5
                feedback.pushInfo(
                    f"Snapped to {snap_x:.2f}, {snap_y:.2f} "
                    f"({moved:.1f} map units away; {snapped_accum:,.0f} upslope cells "
                    f"= {snapped_accum * cell_area / 1e6:,.2f} km²)."
                )

                self.check_canceled(feedback)
                feedback.setProgress(65)
                feedback.pushInfo("4. Delineating the watershed...")
                pour_raster = os.path.join(tmpdir, "pour_point.tif")
                vectors.create_pour_point_raster(snap_x, snap_y, accum_path, pour_raster)

                basin_path = out_basin or os.path.join(tmpdir, "basin.tif")
                self.run_wbt("Watershed", {
                    'd8_pntr': d8_path,
                    'pour_pts': pour_raster,
                    'output': basin_path,
                }, feedback)

                self.check_canceled(feedback)
                feedback.setProgress(85)
                feedback.pushInfo("5. Vectorising the catchment boundary...")
                area = vectors.vectorize_basin(
                    basin_path, out_catchment, min_area=min_part_area,
                )
                feedback.pushInfo(f"Catchment area: {area / 1e6:,.3f} km²")

                if out_pour_point:
                    dem_ds = gdal.Open(dem_path)
                    projection = dem_ds.GetProjection()
                    dem_ds = None
                    vectors.write_point_vector(
                        snap_x, snap_y, out_pour_point, projection,
                        attributes={
                            'accum_cel': float(snapped_accum),
                            'area_m2': float(snapped_accum * cell_area),
                            'moved_m': float(moved),
                        },
                    )
                    results[self.OUT_POUR_POINT] = out_pour_point

                if out_basin:
                    results[self.OUT_BASIN_RASTER] = out_basin

                feedback.setProgress(100)
        except ScimapCanceled:
            feedback.pushInfo("SCIMAP run cancelled.")
            return {}

        results[self.OUT_CATCHMENT] = out_catchment
        return results
