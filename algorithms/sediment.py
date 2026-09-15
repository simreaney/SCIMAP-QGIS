"""SCIMAP Sediment — diffuse pollution / fine sediment risk mapping.

Ported from ``processing/scimap_standard.py`` in the SCIMAP web application.
The land-cover risk weighting that the web app holds in its Parameters
workspace is exposed here as editable tables, so users no longer have to arrive
with an already-weighted risk raster.
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

from ..core import landcover, styling
from ..data import params_xml
from ..data.defaults import (
    CEH_TO_SCIMAP,
    DEFAULT_WEIGHTS,
    RAMP_LANDCOVER,
    UNMAPPED_FALLBACK_CLASS,
    default_remap_matrix,
    default_weights_matrix,
)
from ..localization import tr
from .risk_base import ScimapRiskAlgorithm


class ScimapStandardAlgorithm(ScimapRiskAlgorithm):
    """SCIMAP Sediment risk mapping."""

    INPUT_LC = 'INPUT_LC'
    LC_IS_PREWEIGHTED = 'LC_IS_PREWEIGHTED'
    LC_IS_SCIMAP_CLASSES = 'LC_IS_SCIMAP_CLASSES'
    REMAP_TABLE = 'REMAP_TABLE'
    WEIGHTS_TABLE = 'WEIGHTS_TABLE'
    PARAM_XML = 'PARAM_XML'
    FALLBACK_CLASS = 'FALLBACK_CLASS'
    OUT_SCIMAP_CLASSES = 'OUT_SCIMAP_CLASSES'

    EROSION_LABEL = 'Erosion Risk'
    _ICON = 'icon_sediment.svg'

    def createInstance(self):
        return ScimapStandardAlgorithm()

    def name(self):
        return 'scimapstandard'

    def displayName(self):
        return tr('SCIMAP Sediment')

    def shortHelpString(self):
        return tr(
            'Maps fine sediment and diffuse pollution risk from a DEM, a land '
            'cover map and a rainfall map.\n\n'
            'By default the land cover raster is reclassified using the SCIMAP '
            'class remap and risk weight tables below. Tick "Land cover is '
            'already a risk weighting" to pass a pre-weighted raster through '
            'untouched instead.'
        )

    # ── Risk-weight parameters ──────────────────────────────────────────

    def add_risk_weight_parameters(self):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_LC, tr('Land Cover Map / Risk Weighting')))

        self.addParameter(QgsProcessingParameterBoolean(
            self.LC_IS_PREWEIGHTED,
            tr('Land cover is already a risk weighting (use values as-is)'),
            defaultValue=False,
        ))

        self.addParameter(QgsProcessingParameterBoolean(
            self.LC_IS_SCIMAP_CLASSES,
            tr('Land cover already uses SCIMAP classes (1-7)'),
            defaultValue=False,
        ))

        remap = QgsProcessingParameterMatrix(
            self.REMAP_TABLE,
            tr('Land cover class -> SCIMAP class'),
            headers=[tr('Land cover ID'), tr('SCIMAP class')],
            numberRows=len(CEH_TO_SCIMAP),
            defaultValue=default_remap_matrix(),
            optional=True,
        )
        self.addParameter(remap)

        weights = QgsProcessingParameterMatrix(
            self.WEIGHTS_TABLE,
            tr('SCIMAP class -> risk weight'),
            headers=[tr('SCIMAP class'), tr('Risk weight')],
            numberRows=len(DEFAULT_WEIGHTS),
            defaultValue=default_weights_matrix(),
            optional=True,
        )
        self.addParameter(weights)

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

    def add_extra_output_parameters(self):
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_SCIMAP_CLASSES, tr('SCIMAP Land Cover Classes'),
            optional=True, createByDefault=False))

    # ── Risk weight ─────────────────────────────────────────────────────

    def build_risk_weight(self, parameters, context, feedback, dem_path, tmpdir, hydro):
        lc_layer = self.parameterAsRasterLayer(parameters, self.INPUT_LC, context)
        preweighted = self.parameterAsBool(parameters, self.LC_IS_PREWEIGHTED, context)
        already_scimap = self.parameterAsBool(parameters, self.LC_IS_SCIMAP_CLASSES, context)
        fallback_class = self.parameterAsInt(parameters, self.FALLBACK_CLASS, context)

        lc_path = self.align_input(
            lc_layer, dem_path, tmpdir, 'landcover_aligned.tif',
            feedback, 'Land cover map', resample='near',
        )
        lc_ds = gdal.Open(lc_path)
        lc_arr = lc_ds.GetRasterBand(1).ReadAsArray().astype(np.float32, copy=False)
        lc_nodata = lc_ds.GetRasterBand(1).GetNoDataValue()
        lc_ds = None
        if lc_nodata is not None:
            lc_arr = np.where(lc_arr == lc_nodata, np.nan, lc_arr)

        lc_arr = np.where(hydro.mask_arr, lc_arr, np.nan)

        if preweighted:
            feedback.pushInfo("Using the land cover raster directly as a risk weighting.")
            self._scimap_classes = None
            return lc_arr

        weights = self._resolve_weights(parameters, context, feedback)
        remap = landcover.matrix_to_lookup(
            self.parameterAsMatrix(parameters, self.REMAP_TABLE, context), int,
        ) or CEH_TO_SCIMAP

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
        self._scimap_classes = scimap_classes
        return risk_weight

    def _resolve_weights(self, parameters, context, feedback):
        """Risk weights from the XML parameter set if given, else the table."""
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

    # ── Extra output ────────────────────────────────────────────────────

    def write_extra_outputs(self, parameters, context, feedback, hydro, tmpdir):
        out_classes = self.parameterAsOutputLayer(parameters, self.OUT_SCIMAP_CLASSES, context)
        scimap_classes = getattr(self, '_scimap_classes', None)
        if not out_classes or scimap_classes is None:
            return {}

        feedback.pushInfo("Writing SCIMAP land cover class raster...")
        classes = np.where(np.isfinite(scimap_classes), scimap_classes, 0)
        self.save_raster(classes, out_classes, hydro.slope_ds, gdal.GDT_Byte, hydro.mask_arr)
        styling.style_output(
            context, out_classes,
            self.colour_ramp_name(parameters, context, RAMP_LANDCOVER),
        )
        return {self.OUT_SCIMAP_CLASSES: out_classes}
