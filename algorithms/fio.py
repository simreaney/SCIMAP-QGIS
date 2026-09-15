"""SCIMAP FIO — seasonal faecal indicator organism risk mapping.

Ported from ``processing/scimap_fio.py`` in the SCIMAP web application. The
hydrology is identical to SCIMAP Sediment; the risk weighting comes from an FIO
concentration raster (CFU) divided by a normalisation constant instead of from
land-cover weights.

The web app picks a season and looks up ``<season>_fio.tif`` and
``<season>_rain.tif`` in its bundled national dataset. With no data directory,
the plugin asks for those two rasters directly — choose the pair for the season
you want to map.
"""

import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
)

from ..data.defaults import FIO_NORMALISE
from ..localization import tr
from .risk_base import ScimapRiskAlgorithm


class ScimapFioAlgorithm(ScimapRiskAlgorithm):
    """SCIMAP FIO risk mapping."""

    INPUT_FIO = 'INPUT_FIO'
    FIO_NORMALISE_VALUE = 'FIO_NORMALISE'

    EROSION_LABEL = 'FIO Delivery Risk'
    _ICON = 'icon_fio.svg'

    def createInstance(self):
        return ScimapFioAlgorithm()

    def name(self):
        return 'scimapfio'

    def displayName(self):
        return tr('SCIMAP FIO')

    def shortHelpString(self):
        return tr(
            'Maps faecal indicator organism (FIO) delivery risk from a DEM, an '
            'FIO concentration raster and a rainfall map.\n\n'
            'Supply the FIO and rainfall rasters for the season you want to map. '
            'FIO values are divided by the normalisation constant before being '
            'used as the risk weighting.'
        )

    def add_risk_weight_parameters(self):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_FIO, tr('FIO Concentration Raster (CFU)')))

        self.addParameter(QgsProcessingParameterNumber(
            self.FIO_NORMALISE_VALUE,
            tr('FIO normalisation constant (CFU)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=FIO_NORMALISE,
            minValue=1e-30,
        ))

    def build_risk_weight(self, parameters, context, feedback, dem_path, tmpdir, hydro):
        fio_layer = self.parameterAsRasterLayer(parameters, self.INPUT_FIO, context)
        normalise = self.parameterAsDouble(parameters, self.FIO_NORMALISE_VALUE, context)
        if normalise == 0:
            normalise = FIO_NORMALISE

        fio_path = self.align_input(
            fio_layer, dem_path, tmpdir, 'fio_aligned.tif',
            feedback, 'FIO concentration raster', resample='bilinear',
        )
        fio_ds = gdal.Open(fio_path)
        fio_arr = fio_ds.GetRasterBand(1).ReadAsArray().astype(np.float32, copy=False)
        fio_nodata = fio_ds.GetRasterBand(1).GetNoDataValue()
        fio_ds = None
        if fio_nodata is not None:
            fio_arr = np.where(fio_arr == fio_nodata, np.nan, fio_arr)

        fio_arr = np.where(hydro.mask_arr, fio_arr, np.nan)

        feedback.pushInfo(f"Normalising FIO values by {normalise:g} CFU.")
        return fio_arr / normalise
