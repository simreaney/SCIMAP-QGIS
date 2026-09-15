"""Network Index — connectivity from a DEM alone.

The SCIMAP network index of connectivity, computed with uniform rainfall so it
depends only on terrain. Useful on its own, and as an input to SCIMAP Flood.
"""

import logging
import tempfile

import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
)

from ..core import plotting, styling
from ..data.defaults import RAMP_CONNECTIVITY
from ..localization import tr
from .base import ScimapAlgorithmBase, ScimapCanceled

logger = logging.getLogger(__name__)


class ScimapNetworkIndexAlgorithm(ScimapAlgorithmBase):
    """SCIMAP network index of connectivity from a DEM."""

    INPUT_DEM = 'INPUT_DEM'
    STREAM_THRESHOLD = 'STREAM_THRESHOLD'
    OUT_NETWORK_INDEX = 'OUT_NETWORK_INDEX'
    OUT_WETNESS_CONNECTIVITY_PLOT = 'OUT_WETNESS_CONNECTIVITY_PLOT'
    OUT_WETNESS_CONNECTIVITY_DATA = 'OUT_WETNESS_CONNECTIVITY_DATA'

    _ICON = 'icon_network.svg'

    def createInstance(self):
        return ScimapNetworkIndexAlgorithm()

    def name(self):
        return 'scimapnetworkindex'

    def displayName(self):
        return tr('Network Index')

    def groupId(self):
        return 'scimap_network_index'

    def shortHelpString(self):
        return tr(
            'Computes a SCIMAP hydrological connectivity index from a DEM using '
            'uniform rainfall, so the result reflects terrain alone. Choose between '
            'the Network Index (flow-path trace) and Percentage Downslope Saturated '
            'Length (PDSL) algorithms. Optionally also outputs the Wetness-Connectivity '
            'Curve, a scatter plot (and its underlying dataset) of each cell\'s '
            'topographic wetness index against its connectivity score.'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_DEM, tr('Digital Elevation Model (DEM)')))

        self.addParameter(QgsProcessingParameterNumber(
            self.STREAM_THRESHOLD,
            tr('Stream Initiation Threshold (m²)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=800000.0,
            minValue=0.0))

        self.add_connectivity_method_parameter()
        self.add_colour_ramp_parameter()
        self.add_wbt_parameter()

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_NETWORK_INDEX, tr('Network Index')))

        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUT_WETNESS_CONNECTIVITY_PLOT, tr('Wetness-Connectivity Curve (PNG)'),
            fileFilter='PNG files (*.png)',
            optional=True, createByDefault=False))

        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUT_WETNESS_CONNECTIVITY_DATA, tr('Wetness-Connectivity Curve dataset (CSV)'),
            fileFilter='CSV files (*.csv)',
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        stream_m2_threshold = self.parameterAsDouble(parameters, self.STREAM_THRESHOLD, context)
        connectivity_method = self.connectivity_method_value(parameters, context)
        self.resolve_wbt_executable(parameters, context, feedback)

        out_network_index_path = self.parameterAsOutputLayer(
            parameters, self.OUT_NETWORK_INDEX, context)
        out_plot_path = self.parameterAsFileOutput(
            parameters, self.OUT_WETNESS_CONNECTIVITY_PLOT, context)
        out_data_path = self.parameterAsFileOutput(
            parameters, self.OUT_WETNESS_CONNECTIVITY_DATA, context)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dem_path = dem_layer.source()

                # Stream extraction here uses the user's own threshold rather
                # than the fixed vector-network threshold the risk tools use.
                probe = gdal.Open(dem_path)
                probe_gt = probe.GetGeoTransform()
                probe = None
                cell_area = abs(probe_gt[1] * probe_gt[5])
                stream_cell_threshold = max(1.0, stream_m2_threshold / cell_area)

                hydro = self.run_hydrology(
                    dem_path, tmpdir, feedback, stream_threshold=stream_cell_threshold,
                )
                mask_arr = hydro.mask_arr

                feedback.setProgress(45)
                feedback.pushInfo("3. Loading arrays and computing network index...")
                slope_arr = np.where(mask_arr, hydro.slope_arr, np.nan)
                accum_arr = np.where(mask_arr, hydro.accum_arr, np.nan)

                self.check_canceled(feedback)
                network_index = self.compute_network_connectivity(
                    hydro.d8_arr,
                    accum_arr,
                    slope_arr,
                    np.ones_like(accum_arr, dtype=np.float32),
                    mask_arr,
                    hydro.channel_mask,
                    hydro.dem_fill_arr,
                    method=connectivity_method,
                )
                # Solver output is float64 internally (see core/connectivity.py);
                # written out as GDT_Float32 regardless, so drop the double-width
                # copy now rather than carrying it into styling/save.
                network_index = network_index.astype(np.float32, copy=False)

                wetness_arr = None
                if out_plot_path or out_data_path:
                    feedback.pushInfo(
                        "Computing wetness values for the Wetness-Connectivity Curve..."
                    )
                    wetness_arr = self.compute_twi(
                        accum_arr, slope_arr, np.ones_like(accum_arr, dtype=np.float32))

                hydro.dem_fill_arr = None
                hydro.slope_arr = None

                feedback.setProgress(85)
                feedback.pushInfo("4. Writing connectivity raster...")
                self.save_raster(network_index, out_network_index_path,
                                 hydro.slope_ds, gdal.GDT_Float32, mask_arr)

                feedback.setProgress(100)
        except ScimapCanceled:
            feedback.pushInfo("SCIMAP run cancelled.")
            return {}

        output_layer_name = tr('PDSL') if connectivity_method == 'pdsl' else tr('Network Index')
        styling.style_output(
            context, out_network_index_path,
            self.colour_ramp_name(parameters, context, RAMP_CONNECTIVITY),
            layer_name=output_layer_name,
        )

        results = {
            self.OUT_NETWORK_INDEX: out_network_index_path,
        }

        if wetness_arr is not None:
            wet_sample, conn_sample, n_valid = plotting.sample_wetness_connectivity(
                wetness_arr, network_index, mask_arr)
            if n_valid == 0:
                feedback.pushWarning(
                    "No valid wetness/connectivity cells to plot; skipping the "
                    "Wetness-Connectivity Curve."
                )
            else:
                if out_plot_path:
                    try:
                        plotting.save_scatter_plot(wet_sample, conn_sample, out_plot_path)
                        results[self.OUT_WETNESS_CONNECTIVITY_PLOT] = out_plot_path
                    except ImportError:
                        feedback.pushWarning(
                            "matplotlib is not available in this QGIS Python "
                            "environment; skipping the Wetness-Connectivity Curve image."
                        )
                    except Exception as exc:
                        feedback.pushWarning(
                            f"Could not write the Wetness-Connectivity Curve image: {exc}")
                        logger.warning("Wetness-Connectivity Curve plot failed", exc_info=True)
                if out_data_path:
                    try:
                        plotting.save_scatter_dataset(wet_sample, conn_sample, out_data_path)
                        results[self.OUT_WETNESS_CONNECTIVITY_DATA] = out_data_path
                    except Exception as exc:
                        feedback.pushWarning(
                            f"Could not write the Wetness-Connectivity Curve dataset: {exc}")
                        logger.warning("Wetness-Connectivity Curve dataset failed", exc_info=True)

        return results
