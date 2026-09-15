"""Apply Land Cover Risk Weights — build a weighted risk raster on its own.

The same reclassification SCIMAP Sediment performs internally, exposed as a
standalone tool so a weighted raster can be built once, inspected, and reused
across runs or in Processing models.
"""

import os

import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFile,
    QgsProcessingParameterMatrix,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
)

from ..core import landcover, raster_io, styling
from ..data import params_xml
from ..data.defaults import (
    CEH_TO_SCIMAP,
    DEFAULT_WEIGHTS,
    RAMP_LANDCOVER,
    RAMP_SCIMAP,
    UNMAPPED_FALLBACK_CLASS,
    default_remap_matrix,
    default_weights_matrix,
)
from ..localization import tr
from .base import ScimapAlgorithmBase


class ScimapLandcoverWeightsAlgorithm(ScimapAlgorithmBase):
    """Reclassify a land cover raster into SCIMAP risk weights."""

    INPUT_LC = 'INPUT_LC'
    LC_IS_SCIMAP_CLASSES = 'LC_IS_SCIMAP_CLASSES'
    REMAP_TABLE = 'REMAP_TABLE'
    WEIGHTS_TABLE = 'WEIGHTS_TABLE'
    PARAM_XML = 'PARAM_XML'
    FALLBACK_CLASS = 'FALLBACK_CLASS'

    OUT_WEIGHTS = 'OUT_WEIGHTS'
    OUT_SCIMAP_CLASSES = 'OUT_SCIMAP_CLASSES'

    _ICON = 'icon_landcover.svg'

    def createInstance(self):
        return ScimapLandcoverWeightsAlgorithm()

    def name(self):
        return 'landcoverweights'

    def displayName(self):
        return tr('Apply Land Cover Risk Weights')

    def group(self):
        return tr('Preparation')

    def groupId(self):
        return 'scimap_preparation'

    def shortHelpString(self):
        return tr(
            'Reclassifies a land cover raster into SCIMAP risk weights.\n\n'
            'Raw land cover IDs are first mapped to SCIMAP classes 1-7 using the '
            'remap table (pre-filled with the CEH Land Cover Map mapping), then '
            'each class is given its risk weight. Values present in the data but '
            'absent from the weight table fall back to the nominated class.'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_LC, tr('Land Cover Map')))

        self.addParameter(QgsProcessingParameterBoolean(
            self.LC_IS_SCIMAP_CLASSES,
            tr('Land cover already uses SCIMAP classes (1-7)'),
            defaultValue=False,
        ))

        self.addParameter(QgsProcessingParameterMatrix(
            self.REMAP_TABLE,
            tr('Land cover class -> SCIMAP class'),
            headers=[tr('Land cover ID'), tr('SCIMAP class')],
            numberRows=len(CEH_TO_SCIMAP),
            defaultValue=default_remap_matrix(),
            optional=True,
        ))

        self.addParameter(QgsProcessingParameterMatrix(
            self.WEIGHTS_TABLE,
            tr('SCIMAP class -> risk weight'),
            headers=[tr('SCIMAP class'), tr('Risk weight')],
            numberRows=len(DEFAULT_WEIGHTS),
            defaultValue=default_weights_matrix(),
            optional=True,
        ))

        self.addParameter(QgsProcessingParameterFile(
            self.PARAM_XML,
            tr('Parameter set XML (overrides the risk weight table)'),
            behavior=QgsProcessingParameterFile.File,
            extension='xml',
            fileFilter='XML files (*.xml)',
            optional=True,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.FALLBACK_CLASS,
            tr('SCIMAP class used for unmapped land cover values'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=UNMAPPED_FALLBACK_CLASS,
            minValue=0,
        ))

        self.add_colour_ramp_parameter()

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_WEIGHTS, tr('Land Cover Risk Weighting')))

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_SCIMAP_CLASSES, tr('SCIMAP Land Cover Classes'),
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):
        lc_layer = self.parameterAsRasterLayer(parameters, self.INPUT_LC, context)
        already_scimap = self.parameterAsBool(parameters, self.LC_IS_SCIMAP_CLASSES, context)
        fallback_class = self.parameterAsInt(parameters, self.FALLBACK_CLASS, context)

        out_weights = self.parameterAsOutputLayer(parameters, self.OUT_WEIGHTS, context)
        out_classes = self.parameterAsOutputLayer(parameters, self.OUT_SCIMAP_CLASSES, context)

        lc_ds = gdal.Open(lc_layer.source())
        if lc_ds is None:
            raise RuntimeError(f"Could not open land cover raster: {lc_layer.source()}")
        band = lc_ds.GetRasterBand(1)
        lc_arr = band.ReadAsArray().astype(np.float32, copy=False)
        nodata = band.GetNoDataValue()
        if nodata is not None:
            lc_arr = np.where(lc_arr == nodata, np.nan, lc_arr)

        weights = self._resolve_weights(parameters, context, feedback)
        remap = landcover.matrix_to_lookup(
            self.parameterAsMatrix(parameters, self.REMAP_TABLE, context), int,
        ) or CEH_TO_SCIMAP

        feedback.setProgress(30)
        feedback.pushInfo(
            "Applying SCIMAP risk weights: "
            + ", ".join(f"{k}={v:g}" for k, v in sorted(weights.items()))
        )

        risk_weight, scimap_classes = landcover.build_risk_weight(
            lc_arr, weights, remap,
            already_scimap=already_scimap,
            fallback_class=fallback_class,
            feedback=feedback,
        )

        feedback.setProgress(70)
        raster_io.save_raster(risk_weight, out_weights, lc_ds, gdal.GDT_Float32)

        results = {self.OUT_WEIGHTS: out_weights}
        if out_classes:
            classes = np.where(np.isfinite(scimap_classes), scimap_classes, 0)
            raster_io.save_raster(classes, out_classes, lc_ds, gdal.GDT_Byte)
            results[self.OUT_SCIMAP_CLASSES] = out_classes
            styling.style_output(
                context, out_classes,
                self.colour_ramp_name(parameters, context, RAMP_LANDCOVER))

        lc_ds = None
        feedback.setProgress(100)

        styling.style_output(
            context, out_weights,
            self.colour_ramp_name(parameters, context, RAMP_SCIMAP))
        return results

    def _resolve_weights(self, parameters, context, feedback):
        xml_path = self.parameterAsFile(parameters, self.PARAM_XML, context)
        if xml_path:
            xml_path = os.path.expanduser(xml_path)
            if os.path.isfile(xml_path):
                name, weights = params_xml.read_weights(xml_path)
                feedback.pushInfo(f"Loaded parameter set '{name}' from {xml_path}")
                return weights
            feedback.pushWarning(
                f"Parameter set XML not found, using the risk weight table: {xml_path}"
            )

        weights = landcover.matrix_to_lookup(
            self.parameterAsMatrix(parameters, self.WEIGHTS_TABLE, context), float,
        )
        return weights or dict(DEFAULT_WEIGHTS)
