"""detector — shared engine for the operational-intelligence portfolio.

Re-exports the four frozen primitives so callers can do:
    from detector import deseasonalize, robust_scale, downward_cusum, score_series
"""

from detector.engine import (
    deseasonalize,
    robust_scale,
    downward_cusum,
    score_series,
)

__all__ = [
    "deseasonalize",
    "robust_scale",
    "downward_cusum",
    "score_series",
]
