"""Tests for the grain-aware deseasonalize (v0.2.0 spec).

Three test cases per the spec:
1. Regression  — default args reproduce v0.1.0 bit-identical output.
2. Daily grain — weekend dip removed; per-dow medians ≈ 0.
3. Fail fast   — unsupported grain raises ValueError.
"""

import numpy as np
import pandas as pd
import pytest

from detector import deseasonalize


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_hourly_frame(weeks: int = 4, seed: int = 42) -> pd.DataFrame:
    """Synthetic hourly frame with a dow×hour seasonal pattern + noise."""
    rng = np.random.default_rng(seed)
    hours = weeks * 7 * 24
    ts = pd.date_range("2026-01-05", periods=hours, freq="h")  # Monday
    dow = ts.dayofweek.to_numpy(dtype=float)
    hod = ts.hour.to_numpy(dtype=float)
    seasonal = dow * 2.0 + hod * 0.3          # deterministic pattern
    noise = rng.normal(0, 0.5, size=hours)
    return pd.DataFrame({"hour": ts, "value": seasonal + noise + 100.0})


def _v010_deseasonalize(g, signal):
    """Frozen copy of the v0.1.0 implementation for regression comparison."""
    s = g[signal].astype(float)
    grp_med = s.groupby(
        [g["hour"].dt.dayofweek, g["hour"].dt.hour]
    ).transform("median")
    return (s - grp_med).to_numpy()


def _make_daily_frame(weeks: int = 8, seed: int = 99) -> pd.DataFrame:
    """Daily frame where weekends (Sat/Sun) are 20% lower."""
    rng = np.random.default_rng(seed)
    days = weeks * 7
    ts = pd.date_range("2026-01-05", periods=days, freq="D")  # Monday
    base = 100.0
    values = np.full(days, base)
    for i, d in enumerate(ts):
        if d.dayofweek >= 5:  # Sat=5, Sun=6
            values[i] = base * 0.80
    values += rng.normal(0, 0.5, size=days)
    return pd.DataFrame({"date": ts, "metric": values})


# ---------------------------------------------------------------------------
# 1. Regression: bit-identical to v0.1.0 with default args
# ---------------------------------------------------------------------------

def test_hourly_default_args_match_v010():
    df = _make_hourly_frame()
    result_v020 = deseasonalize(df, "value")
    result_v010 = _v010_deseasonalize(df, "value")
    np.testing.assert_array_equal(
        result_v020, result_v010,
        err_msg="Default-args v0.2.0 output diverged from v0.1.0",
    )


def test_hourly_explicit_grain_matches_default():
    """Explicitly passing grain='hourly' must equal the default call."""
    df = _make_hourly_frame()
    default = deseasonalize(df, "value")
    explicit = deseasonalize(df, "value", time_col="hour", grain="hourly")
    np.testing.assert_array_equal(default, explicit)


# ---------------------------------------------------------------------------
# 2. Daily grain: weekend dip removed
# ---------------------------------------------------------------------------

def test_daily_grain_removes_dow_pattern():
    df = _make_daily_frame()
    resid = deseasonalize(df, "metric", time_col="date", grain="daily")

    # After deseasonalizing, the per-dow median of residuals should be ≈ 0
    result = pd.DataFrame({"dow": df["date"].dt.dayofweek, "resid": resid})
    dow_medians = result.groupby("dow")["resid"].median()

    # Each dow-group median should be near zero (tolerance for noise)
    np.testing.assert_allclose(
        dow_medians.values, 0.0, atol=1e-10,
        err_msg=f"Per-dow medians should be ~0 after daily deseasonalize: {dow_medians.to_dict()}",
    )


def test_daily_grain_output_length():
    df = _make_daily_frame()
    resid = deseasonalize(df, "metric", time_col="date", grain="daily")
    assert len(resid) == len(df)


# ---------------------------------------------------------------------------
# 3. Fail fast: unsupported grain
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_grain", ["weekly", "monthly", "", None, 42])
def test_invalid_grain_raises(bad_grain):
    df = _make_hourly_frame()
    with pytest.raises(ValueError, match="grain must be"):
        deseasonalize(df, "value", grain=bad_grain)
