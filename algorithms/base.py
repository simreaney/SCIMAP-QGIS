"""Shared base class for every SCIMAP Processing algorithm.

Holds the parameters, WhiteboxTools plumbing and hydrology preamble that the
risk-mapping tools have in common, so each algorithm module only carries the
maths that makes it distinct.
"""

import logging
import os

import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
)
from qgis.PyQt.QtGui import QIcon

from ..core import raster_io, vectors, wbt
from ..core.connectivity import (
    compute_connectivity_flow_path_trace,
    compute_network_connectivity,
    compute_pdsl,
    compute_twi,
)
from ..core.erosion import compute_erosion_risk, normalise_percentile
from ..core._numba import describe_backend
from ..data.defaults import COLOUR_RAMPS, RAMP_DEFAULTS_OPTION
from ..localization import tr

logger = logging.getLogger(__name__)

# Match the web application's stream-network extraction threshold; the
# resulting channel cells are the terminal targets for connectivity routing.
STREAM_VECTOR_THRESHOLD = 250


class HydrologyResult:
    """Paths and arrays produced by the shared hydrology preamble."""

    __slots__ = (
        'dem_fill_path', 'slope_path', 'accum_path', 'd8_path', 'stream_path',
        'stream_vector_path', 'd8_accum_path',
        'slope_ds', 'slope_arr', 'accum_arr', 'd8_arr',
        'dem_fill_arr', 'mask_arr', 'channel_mask', 'cell_area',
    )

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class ScimapAlgorithmBase(QgsProcessingAlgorithm):
    """Common parameters and helpers for the SCIMAP tools."""

    WBT_EXECUTABLE = 'WBT_EXECUTABLE'
    COLOUR_RAMP = 'COLOUR_RAMP'
    CONNECTIVITY_METHOD = 'CONNECTIVITY_METHOD'

    # Values correspond by index to CONNECTIVITY_METHODS below.
    _CONNECTIVITY_METHOD_VALUES = ('flow_path_trace', 'pdsl')

    _ICON = 'icon.svg'

    def __init__(self):
        super().__init__()
        self._wbt_executable = ''

    def flags(self):
        # Use threads responsibly when integrating GDAL.
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def group(self):
        return tr('Risk Mapping')

    def groupId(self):
        return 'scimap_risk_mapping'

    def icon(self):
        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'icons', self._ICON)
        return QIcon(icon_path)

    # ── Shared parameters ───────────────────────────────────────────────

    def add_wbt_parameter(self):
        self.addParameter(QgsProcessingParameterFile(
            self.WBT_EXECUTABLE,
            tr('WhiteboxTools executable; leave blank to auto-detect'),
            behavior=QgsProcessingParameterFile.File,
            extension='*',
            fileFilter='All files (*)',
            optional=True,
        ))

    def add_colour_ramp_parameter(self):
        """Offer per-layer SCIMAP defaults, or one ramp across every output."""
        self.addParameter(QgsProcessingParameterEnum(
            self.COLOUR_RAMP,
            tr('Colour ramp for output styling'),
            options=[RAMP_DEFAULTS_OPTION] + COLOUR_RAMPS,
            defaultValue=0,
        ))

    def add_connectivity_method_parameter(self):
        """Let the user choose the hydrological connectivity algorithm."""
        self.addParameter(QgsProcessingParameterEnum(
            self.CONNECTIVITY_METHOD,
            tr('Connectivity algorithm'),
            options=[
                tr('Network Index (flow-path trace)'),
                tr('Percentage Downslope Saturated Length (PDSL)'),
            ],
            defaultValue=0,
        ))

    def connectivity_method_value(self, parameters, context):
        """Resolve the CONNECTIVITY_METHOD enum to a solver name."""
        if self.CONNECTIVITY_METHOD not in parameters:
            return self._CONNECTIVITY_METHOD_VALUES[0]
        index = self.parameterAsEnum(parameters, self.CONNECTIVITY_METHOD, context)
        try:
            return self._CONNECTIVITY_METHOD_VALUES[index]
        except IndexError:
            return self._CONNECTIVITY_METHOD_VALUES[0]

    def resolve_wbt_executable(self, parameters, context, feedback):
        """Read the WBT parameter and persist it for future runs."""
        wbt_executable = self.parameterAsFile(parameters, self.WBT_EXECUTABLE, context)
        self._wbt_executable = ''
        if wbt_executable:
            wbt_executable = os.path.expanduser(wbt_executable)
            if os.path.isfile(wbt_executable):
                self._wbt_executable = wbt_executable
                wbt.store_executable(wbt_executable)
            else:
                feedback.pushInfo(
                    f"Provided WhiteboxTools path does not exist or is not a file: {wbt_executable}"
                )
        return self._wbt_executable

    def colour_ramp_name(self, parameters, context, default_name):
        """Resolve the ramp for one output, honouring the per-layer default."""
        index = self.parameterAsEnum(parameters, self.COLOUR_RAMP, context)
        if index <= 0:
            return default_name
        try:
            return COLOUR_RAMPS[index - 1]
        except IndexError:
            return default_name

    # ── Cancellation ────────────────────────────────────────────────────

    @staticmethod
    def check_canceled(feedback):
        """Raise if the user has cancelled, so long runs stop promptly."""
        if feedback is not None and feedback.isCanceled():
            raise ScimapCanceled()

    # ── Delegation to core ──────────────────────────────────────────────

    def run_wbt(self, tool_name, args_dict, feedback):
        wbt.run_wbt(tool_name, args_dict, feedback, self._wbt_executable)

    def save_raster(self, np_array, output_path, reference_ds, data_type,
                    mask_arr=None, wbt_compatible=False):
        raster_io.save_raster(
            np_array, output_path, reference_ds, data_type, mask_arr, wbt_compatible,
        )

    def compute_erosion_risk(self, accum_array, slope_deg, cell_area, use_stream_power=True):
        return compute_erosion_risk(accum_array, slope_deg, cell_area, use_stream_power)

    def normalise_percentile(self, array, p_low=5, p_high=95, max_samples=None):
        return normalise_percentile(array, p_low, p_high, max_samples)

    def compute_twi(self, accum_array, slope_array, rainfall_scaled_array):
        return compute_twi(accum_array, slope_array, rainfall_scaled_array)

    def compute_network_connectivity(self, d8_array, accum_array, slope_array,
                                     rainfall_scaled_array, mask_array=None,
                                     channel_mask=None, dem_array=None,
                                     progress_callback=None, method='flow_path_trace'):
        return compute_network_connectivity(
            d8_array, accum_array, slope_array, rainfall_scaled_array,
            mask_array, channel_mask, dem_array, progress_callback, method,
        )

    def compute_pdsl(self, d8_array, twi_array, mask_array=None,
                     channel_mask=None, dem_array=None, progress_callback=None):
        return compute_pdsl(
            d8_array, twi_array, mask_array, channel_mask, dem_array, progress_callback,
        )

    def compute_connectivity_flow_path_trace(self, d8_array, twi_array, mask_array=None,
                                             channel_mask=None, dem_array=None,
                                             progress_callback=None):
        return compute_connectivity_flow_path_trace(
            d8_array, twi_array, mask_array, channel_mask, dem_array, progress_callback,
        )

    def describe_connectivity_backend(self):
        return describe_backend()

    def create_pour_point_raster_safe(self, x, y, template_path, output_path, feedback=None):
        vectors.create_pour_point_raster(x, y, template_path, output_path)

    def create_points_raster_safe(self, points, template_path, output_path, feedback=None):
        vectors.create_points_raster(points, template_path, output_path)

    def run_cost_distance_safe(self, source_path, cost_path, output_path, feedback):
        return vectors.run_wbt_cost_distance(
            lambda tool_name, args: self.run_wbt(tool_name, args, feedback),
            source_path, cost_path, output_path,
        )

    # ── Shared pipeline steps ───────────────────────────────────────────

    def align_input(self, layer, dem_path, tmpdir, filename, feedback, label,
                    resample='near'):
        """Return a path to *layer* resampled onto the DEM grid when needed."""
        return raster_io.align_to_reference(
            layer.source(), dem_path, os.path.join(tmpdir, filename),
            resample=resample, feedback=feedback, label=label,
        )

    def run_hydrology(self, dem_path, tmpdir, feedback, breach=True,
                      stream_threshold=STREAM_VECTOR_THRESHOLD):
        """Breach depressions, then derive slope, FD8 accumulation, D8 and streams.

        Returns a :class:`HydrologyResult` holding both the intermediate paths
        and the arrays every downstream SCIMAP calculation needs.
        """
        self.check_canceled(feedback)
        feedback.setProgress(1)

        if breach:
            feedback.pushInfo("1. Filling DEM depressions...")
            dem_fill_path = os.path.join(tmpdir, "dem_fill.tif")
            # Using QGIS native WhiteboxTools integration workaround for Qt6
            self.run_wbt("BreachDepressions", {
                'dem': dem_path,
                'output': dem_fill_path,
            }, feedback)
        else:
            dem_fill_path = dem_path

        self.check_canceled(feedback)
        feedback.setProgress(10)
        feedback.pushInfo("2. Calculating Slope, FD8 Flow Accumulation, and D8 Pointer...")
        slope_path = os.path.join(tmpdir, "slope.tif")
        self.run_wbt("Slope", {
            'dem': dem_fill_path, 'output': slope_path,
        }, feedback)

        accum_path = os.path.join(tmpdir, "accum.tif")
        try:
            self.run_wbt("FD8FlowAccumulation", {
                'dem': dem_fill_path,
                'out_type': 'cells',
                'exponent': 2.0,
                'output': accum_path,
            }, feedback)
        except Exception:
            # Fallback for Whitebox builds that expose "i" instead of "dem".
            self.run_wbt("FD8FlowAccumulation", {
                'i': dem_fill_path,
                'out_type': 'cells',
                'exponent': 2.0,
                'output': accum_path,
            }, feedback)

        self.check_canceled(feedback)
        # D8 pointer is still required by connectivity routing and RasterStreamsToVector.
        d8_path = os.path.join(tmpdir, "d8.tif")
        feedback.pushInfo("D8 pointer encoding: Whitebox style (ESRI pointer flag omitted)")
        self.run_wbt("D8Pointer", {
            'dem': dem_fill_path,
            'output': d8_path,
            'esri_pntr': False,
        }, feedback)

        stream_path = os.path.join(tmpdir, "stream.tif")
        self.run_wbt("ExtractStreams", {
            'flow_accum': accum_path,
            'output': stream_path,
            'threshold': stream_threshold,
        }, feedback)

        # RasterStreamsToVector traces the network by following the D8 pointer
        # from cell to cell, so the stream mask it walks must be D8-consistent.
        # Thresholding the FD8 (dispersive) accumulation instead leaves cells
        # that pass the threshold without their D8 downstream neighbour also
        # passing it, which breaks the traced lines into many disconnected
        # fragments. Build a separate D8-based mask for vectorisation only;
        # every other use of stream_path/channel_mask (connectivity routing
        # termini, channel risk masking) stays on the FD8-based one above to
        # match the web application's science.
        d8_accum_path = os.path.join(tmpdir, "d8_accum.tif")
        self.run_wbt("D8FlowAccumulation", {
            'i': dem_fill_path,
            'out_type': 'cells',
            'output': d8_accum_path,
        }, feedback)

        stream_vector_path = os.path.join(tmpdir, "stream_vector.tif")
        self.run_wbt("ExtractStreams", {
            'flow_accum': d8_accum_path,
            'output': stream_vector_path,
            'threshold': stream_threshold,
        }, feedback)

        self.check_canceled(feedback)
        feedback.setProgress(22)
        feedback.pushInfo("3. Loading arrays to process SCIMAP logic...")
        slope_ds = gdal.Open(slope_path)
        slope_arr = slope_ds.GetRasterBand(1).ReadAsArray().astype(np.float32, copy=False)

        accum_ds = gdal.Open(accum_path)
        accum_arr = accum_ds.GetRasterBand(1).ReadAsArray().astype(np.float32, copy=False)

        # Only used below to derive channel_mask; there is no need to hold
        # the full raster in memory for the rest of the run.
        stream_ds = gdal.Open(stream_path)
        stream_arr = stream_ds.GetRasterBand(1).ReadAsArray().astype(np.float32, copy=False)

        # compute_network_connectivity always takes the DEM-based downstream
        # routing below when dem_array is given (matching the web app's own
        # wb_utils.compute_network_connectivity dispatch), so the D8 pointer
        # array is never actually read by the connectivity solver in this
        # pipeline; skip loading it into memory and keep only the file, which
        # RasterStreamsToVector still needs.
        d8_arr = None

        dem_fill_ds = gdal.Open(dem_fill_path)
        # float32 matches every other raster in this pipeline (and every
        # SCIMAP output is written out as GDT_Float32 anyway); the numeric
        # kernels in core/connectivity.py upcast to float64 internally only
        # for the duration of their own computation, so nothing downstream
        # needs this array held at double precision for the whole run.
        dem_fill_arr = dem_fill_ds.GetRasterBand(1).ReadAsArray().astype(np.float32, copy=False)

        dem_ds = gdal.Open(dem_path)
        dem_arr = dem_ds.GetRasterBand(1).ReadAsArray()
        dem_nodata = dem_ds.GetRasterBand(1).GetNoDataValue()
        # Create a boolean mask where True means it is valid data
        if dem_nodata is not None:
            mask_arr = (dem_arr != dem_nodata) & np.isfinite(dem_arr)
        else:
            mask_arr = np.isfinite(dem_arr)

        gt = slope_ds.GetGeoTransform()
        cell_area = abs(gt[1] * gt[5])

        return HydrologyResult(
            dem_fill_path=dem_fill_path,
            slope_path=slope_path,
            accum_path=accum_path,
            d8_path=d8_path,
            stream_path=stream_path,
            stream_vector_path=stream_vector_path,
            d8_accum_path=d8_accum_path,
            slope_ds=slope_ds,
            slope_arr=slope_arr,
            accum_arr=accum_arr,
            d8_arr=d8_arr,
            dem_fill_arr=dem_fill_arr,
            mask_arr=mask_arr,
            channel_mask=(stream_arr > 0) & mask_arr,
            cell_area=cell_area,
        )

    def scale_rainfall(self, rain_arr, mask_arr):
        """Normalise rainfall by its in-catchment mean."""
        rain_mean = np.nanmean(rain_arr[mask_arr]) if np.any(mask_arr) else np.nanmean(rain_arr)
        if rain_mean == 0 or np.isnan(rain_mean):
            rain_mean = 1.0
        return rain_arr / rain_mean

    def connectivity_with_progress(self, hydro, rain_scaled, feedback, method='flow_path_trace'):
        """Run the connectivity solver, reporting sub-steps through *feedback*."""
        feedback.setProgress(45)
        method_label = 'PDSL' if method == 'pdsl' else 'Network Index'
        feedback.pushInfo(f"5. Computing {method_label} connectivity...")
        backend_description = self.describe_connectivity_backend()
        feedback.pushInfo(f"Connectivity backend: {backend_description}")
        if 'pure-NumPy fallback' in backend_description:
            feedback.pushInfo(
                "Connectivity is running without Numba acceleration in QGIS Python; "
                "this can be very slow on large rasters."
            )

        def connectivity_progress(progress_value, message=None):
            feedback.setProgress(progress_value)
            if message:
                feedback.pushInfo(message)

        self.check_canceled(feedback)
        connectivity = self.compute_network_connectivity(
            hydro.d8_arr,
            hydro.accum_arr,
            hydro.slope_arr,
            rain_scaled,
            hydro.mask_arr,
            hydro.channel_mask,
            hydro.dem_fill_arr,
            progress_callback=connectivity_progress,
            method=method,
        )
        # The solver above works in float64 internally (matching the web
        # app's implementation exactly, down to the last bit); the output is
        # always written out as GDT_Float32 regardless, so drop to float32
        # here rather than carrying float64 copies through every downstream
        # combination step.
        return connectivity.astype(np.float32, copy=False)

    def combine_scimap_output(self, erosion_risk, connectivity, hydro, rain_scaled,
                              stream_cell_threshold, feedback, routing='massflux'):
        """Combine erosion risk and connectivity into the SCIMAP risk concentration.

        The accumulated risk (erosion x connectivity) is routed with WhiteboxTools
        ``DInfMassFlux`` across the *whole* catchment — not just the channel
        network — matching the web application, then divided by the
        rainfall-weighted catchment area (also routed the same way) to give a
        risk concentration that is valid everywhere in the catchment.

        Returns ``(risk_concentration, channel_risk_concentration)``: the first is
        the ratio computed over the whole catchment, for attributing the instream
        vector network (whose reaches extend well upstream of the stream
        initiation threshold); the second restricts that same ratio to cells at or
        above *stream_cell_threshold*, matching the web application's stream risk
        points.
        """
        feedback.setProgress(58)
        feedback.pushInfo("6. Computing FD8-based rainfall-weighted and SCIMAP routed proxies...")
        tiny = 1e-10
        mask_arr = hydro.mask_arr
        accum_arr_masked = np.where(mask_arr, hydro.accum_arr, np.nan)
        source_risk = erosion_risk * connectivity

        catchment_area = np.abs(accum_arr_masked) * hydro.cell_area

        rainfall_weighted_catchment_area = None
        scimap_routed_accum = None
        if routing == 'massflux':
            # Route both terms, as the web application does: the numerator is
            # area-weighted risk loading and the denominator area-weighted
            # rainfall, so the ratio stays on a common areal basis. Both loadings
            # cover the whole catchment (every valid cell, not just channels), so
            # the routed accumulation reflects risk contributed from the entire
            # upslope area, not only in-channel cells.
            rainfall_weighted_catchment_area = self._route_mass_flux(
                hydro, np.where(np.isfinite(rain_scaled), rain_scaled * hydro.cell_area, 0.0),
                'rainfall', feedback,
            )
            if rainfall_weighted_catchment_area is not None:
                scimap_routed_accum = self._route_mass_flux(
                    hydro,
                    np.where(np.isfinite(source_risk), source_risk * hydro.cell_area, 0.0),
                    'erosion-connectivity risk', feedback,
                )

        if rainfall_weighted_catchment_area is None:
            rainfall_weighted_catchment_area = catchment_area * rain_scaled
        if scimap_routed_accum is None:
            scimap_routed_accum = source_risk * catchment_area

        risk_concentration = scimap_routed_accum / (rainfall_weighted_catchment_area + tiny)
        risk_concentration[~mask_arr] = np.nan

        channel_risk_concentration = np.where(
            np.abs(accum_arr_masked) >= stream_cell_threshold, risk_concentration, np.nan,
        )
        channel_risk_concentration[~mask_arr] = np.nan

        return risk_concentration, channel_risk_concentration

    def _route_mass_flux(self, hydro, loading, label, feedback):
        """Backwards-compatible wrapper around :meth:`route_mass_flux`."""
        array, _path = self.route_mass_flux(hydro, loading, label, feedback)
        return array

    def route_mass_flux(self, hydro, loading, label, feedback,
                        tool='DInfMassFlux', out_path=None, slug=None):
        """Route an area-weighted loading downslope with WhiteboxTools.

        Ported from ``processing/scimap_standard.py``. Returns
        ``(array, out_path)``, with ``array`` ``None`` if the routing fails so
        the caller can fall back to local scaling exactly as the web
        application does.

        *tool* is ``'DInfMassFlux'`` (the SCIMAP default, and what every other
        tool here uses) or ``'D8MassFlux'``, which the SCIMAP-Fitted research
        scripts use. Mixing the two within one ratio is a mistake: D8
        concentrates flow into single-cell threads while D-infinity disperses
        it, so a numerator and denominator routed by different tools differ by
        a systematic hillslope artefact that is pure numerical mismatch.

        *out_path* and *slug* let a caller that routes many loadings — the
        SCIMAP-Fitted ensemble stage routes one per land-cover class — keep
        their intermediates apart. The default derives the filename from
        *label*, which is fine for a single call but collides across a loop.
        """
        tmpdir = os.path.dirname(hydro.dem_fill_path)
        slug = slug or label.split()[0].lower()
        loading_path = os.path.join(tmpdir, f"{slug}_loading.tif")
        eff_path = os.path.join(tmpdir, "efficiency_ones.tif")
        abs_path = os.path.join(tmpdir, "absorption_zero.tif")
        if out_path is None:
            out_path = os.path.join(tmpdir, f"{slug}_routed_accum.tif")

        try:
            feedback.pushInfo(f"Routing {label} downslope ({tool})...")
            self.save_raster(loading, loading_path, hydro.slope_ds,
                             gdal.GDT_Float32, wbt_compatible=True)
            if not os.path.exists(eff_path):
                self.save_raster(np.ones_like(loading), eff_path, hydro.slope_ds,
                                 gdal.GDT_Float32, wbt_compatible=True)
                self.save_raster(np.zeros_like(loading), abs_path, hydro.slope_ds,
                                 gdal.GDT_Float32, wbt_compatible=True)

            self.run_wbt(tool, {
                'dem': hydro.dem_fill_path,
                'loading': loading_path,
                'efficiency': eff_path,
                'absorption': abs_path,
                'output': out_path,
            }, feedback)

            ds = gdal.Open(out_path)
            if ds is None:
                raise RuntimeError(f"{tool} produced no output")
            band = ds.GetRasterBand(1)
            # The mass-flux tool was fed float32 loading/efficiency/absorption
            # rasters above and this result is only ever combined with other
            # float32 arrays, so read it back at the same precision instead
            # of doubling its footprint for no benefit.
            arr = band.ReadAsArray().astype(np.float32, copy=False)
            nodata = band.GetNoDataValue()
            ds = None
            if nodata is not None:
                arr = np.where(arr == nodata, np.nan, arr)
            return np.where(hydro.mask_arr, arr, np.nan), out_path
        except Exception as exc:
            feedback.pushWarning(
                f"Mass-flux routing of {label} failed ({exc}); "
                "using local scaling fallback."
            )
            logger.warning("%s routing failed for %s", tool, label, exc_info=True)
            return None, out_path


class ScimapCanceled(RuntimeError):
    """Raised internally when the user cancels a running algorithm."""

    def __init__(self):
        super().__init__("Algorithm canceled by user.")
