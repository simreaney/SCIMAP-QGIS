"""Optional Numba acceleration.

Every kernel in :mod:`core` has a pure-NumPy fallback, so the plugin works
without Numba installed — it is simply slower on large catchments.
"""

try:
    from numba import njit, prange
except ModuleNotFoundError:  # pragma: no cover - depends on the QGIS install
    njit = None
    prange = range


def describe_backend():
    """Human-readable description of the active connectivity backend."""
    if njit is not None:
        return 'SAGA-style flow-path trace with Numba parallel kernel'
    return 'SAGA-style flow-path trace with pure-NumPy fallback'
