"""Backwards-compatible entry point.

The plugin's algorithms used to live in this single module. They now sit in the
:mod:`algorithms` package, with the shared maths in :mod:`core`. This shim keeps
``from .scimap_algorithm import ScimapStandardAlgorithm`` and friends working for
any external script or saved Processing model that still imports from here.

New code should import from the packages directly.
"""

from .algorithms.base import ScimapAlgorithmBase, ScimapCanceled
from .algorithms.catchment import ScimapCatchmentAlgorithm
from .algorithms.export import ScimapExportResultsAlgorithm
from .algorithms.fio import ScimapFioAlgorithm
from .algorithms.fitted_base import ScimapFittedAlgorithmBase
from .algorithms.fitted_calibrate import ScimapFittedCalibrateAlgorithm
from .algorithms.fitted_maps import ScimapFittedMapsAlgorithm
from .algorithms.fitted_stats import ScimapFittedStatsAlgorithm
from .algorithms.flood import ScimapFloodAlgorithm
from .algorithms.network_index import ScimapNetworkIndexAlgorithm
from .algorithms.overland_flow import ScimapOverlandFlowDistanceAlgorithm
from .algorithms.reclass import ScimapLandcoverWeightsAlgorithm
from .algorithms.risk_base import ScimapRiskAlgorithm
from .algorithms.sediment import ScimapStandardAlgorithm
from .core.connectivity import (
    WBT_D8,
    compute_connectivity_flow_path_trace,
    compute_network_connectivity,
    compute_twi,
)
from .core.erosion import compute_erosion_risk, normalise_percentile

__all__ = [
    'ScimapAlgorithmBase',
    'ScimapCanceled',
    'ScimapRiskAlgorithm',
    'ScimapStandardAlgorithm',
    'ScimapFioAlgorithm',
    'ScimapFittedAlgorithmBase',
    'ScimapFittedCalibrateAlgorithm',
    'ScimapFittedStatsAlgorithm',
    'ScimapFittedMapsAlgorithm',
    'ScimapNetworkIndexAlgorithm',
    'ScimapFloodAlgorithm',
    'ScimapOverlandFlowDistanceAlgorithm',
    'ScimapCatchmentAlgorithm',
    'ScimapLandcoverWeightsAlgorithm',
    'ScimapExportResultsAlgorithm',
    'WBT_D8',
    'compute_twi',
    'compute_network_connectivity',
    'compute_connectivity_flow_path_trace',
    'compute_erosion_risk',
    'normalise_percentile',
]
