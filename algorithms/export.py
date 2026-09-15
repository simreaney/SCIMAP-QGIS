"""Export SCIMAP Results — the download formats the web application offers.

Converts a finished SCIMAP result into KML, GeoPackage, a stream-risk point
layer and a Cloud-Optimised GeoTIFF, matching the web app's download menu.
"""

import os

from qgis.core import (
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorDestination,
    QgsProcessingParameterVectorLayer,
)

from ..core import vectors
from ..localization import tr
from .base import ScimapAlgorithmBase


class ScimapExportResultsAlgorithm(ScimapAlgorithmBase):
    """Export SCIMAP outputs to shareable formats."""

    INPUT_RASTER = 'INPUT_RASTER'
    INPUT_VECTOR = 'INPUT_VECTOR'
    POINT_FIELD = 'POINT_FIELD'
    POSITIVE_ONLY = 'POSITIVE_ONLY'

    OUT_POINTS = 'OUT_POINTS'
    OUT_GPKG = 'OUT_GPKG'
    OUT_KML = 'OUT_KML'
    OUT_COG = 'OUT_COG'

    _ICON = 'icon.svg'

    def createInstance(self):
        return ScimapExportResultsAlgorithm()

    def name(self):
        return 'exportresults'

    def displayName(self):
        return tr('Export SCIMAP Results')

    def group(self):
        return tr('Export')

    def groupId(self):
        return 'scimap_export'

    def shortHelpString(self):
        return tr(
            'Converts SCIMAP outputs to the formats the SCIMAP web application '
            'offers for download: a stream-risk point layer and Cloud-Optimised '
            'GeoTIFF from a result raster, and GeoPackage / KML from a result '
            'vector. Leave any output blank to skip it.'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_RASTER, tr('SCIMAP Result Raster'), optional=True))

        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT_VECTOR, tr('SCIMAP Result Vector'), optional=True))

        self.addParameter(QgsProcessingParameterString(
            self.POINT_FIELD, tr('Field name for exported point values'),
            defaultValue='scimap_risk'))

        self.addParameter(QgsProcessingParameterBoolean(
            self.POSITIVE_ONLY,
            tr('Export only cells with values greater than zero'),
            defaultValue=False))

        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUT_POINTS, tr('Result as Points'),
            optional=True, createByDefault=False))

        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUT_GPKG, tr('Result as GeoPackage'),
            optional=True, createByDefault=False))

        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUT_KML, tr('Result as KML'),
            fileFilter='KML files (*.kml)',
            optional=True, createByDefault=False))

        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUT_COG, tr('Result as Cloud-Optimised GeoTIFF'),
            fileFilter='GeoTIFF files (*.tif)',
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):
        raster_layer = self.parameterAsRasterLayer(parameters, self.INPUT_RASTER, context)
        vector_layer = self.parameterAsVectorLayer(parameters, self.INPUT_VECTOR, context)
        field_name = self.parameterAsString(parameters, self.POINT_FIELD, context) or 'scimap_risk'
        positive_only = self.parameterAsBool(parameters, self.POSITIVE_ONLY, context)

        out_points = self.parameterAsOutputLayer(parameters, self.OUT_POINTS, context)
        out_gpkg = self.parameterAsOutputLayer(parameters, self.OUT_GPKG, context)
        out_kml = self.parameterAsFileOutput(parameters, self.OUT_KML, context)
        out_cog = self.parameterAsFileOutput(parameters, self.OUT_COG, context)

        results = {}
        steps = [bool(out_points), bool(out_gpkg), bool(out_kml), bool(out_cog)]
        if not any(steps):
            raise RuntimeError("Choose at least one output format to export.")

        if out_points:
            if raster_layer is None:
                raise RuntimeError("A result raster is required to export points.")
            feedback.pushInfo("Exporting raster cells as points...")
            n_points = vectors.raster_to_point_vector(
                raster_layer.source(), out_points,
                field_name=field_name, positive_only=positive_only,
            )
            feedback.pushInfo(f"Wrote {n_points} points.")
            results[self.OUT_POINTS] = out_points
        feedback.setProgress(30)

        if out_gpkg:
            if vector_layer is None:
                raise RuntimeError("A result vector is required to export a GeoPackage.")
            feedback.pushInfo("Exporting vector as GeoPackage...")
            vectors.copy_vector(vector_layer.source(), out_gpkg)
            results[self.OUT_GPKG] = out_gpkg
        feedback.setProgress(55)

        if out_kml:
            source = vector_layer.source() if vector_layer is not None else out_points
            if not source:
                raise RuntimeError("A result vector is required to export KML.")
            feedback.pushInfo("Exporting vector as KML...")
            vectors.export_vector_kml(
                source, out_kml,
                source_wkt=(vector_layer.crs().toWkt() if vector_layer is not None else None),
                layer_name=os.path.splitext(os.path.basename(out_kml))[0],
            )
            results[self.OUT_KML] = out_kml
        feedback.setProgress(80)

        if out_cog:
            if raster_layer is None:
                raise RuntimeError("A result raster is required to export a COG.")
            feedback.pushInfo("Writing Cloud-Optimised GeoTIFF...")
            vectors.write_cog(raster_layer.source(), out_cog)
            results[self.OUT_COG] = out_cog

        feedback.setProgress(100)
        return results
