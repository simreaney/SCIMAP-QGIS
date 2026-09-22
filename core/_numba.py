"""Optional Numba acceleration.

Every kernel in :mod:`core` has a pure-NumPy fallback, so the plugin works
without Numba installed — it is simply slower on large catchments.
"""

try:
    from numba import njit, prange
except Exception:  # pragma: no cover - depends on the QGIS install
    # Deliberately broader than ModuleNotFoundError. Numba can be present but
    # unloadable -- a mismatched or unsigned llvmlite raises OSError on import,
    # for instance -- and an optional accelerator must never be able to stop
    # the whole plugin loading when a correct fallback is right here.
    njit = None
    prange = range


def describe_backend():
    """Human-readable description of the active connectivity backend."""
    if njit is not None:
        return 'SAGA-style flow-path trace with Numba parallel kernel'
    return 'SAGA-style flow-path trace with pure-NumPy fallback'
