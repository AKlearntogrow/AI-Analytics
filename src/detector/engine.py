"""
engine.py â€” shared detector engine for the operational-intelligence portfolio.

Frozen from the Hex notebooks (Models 01-03) after a diff-and-reconcile pass.
These four functions are the machinery every model reuses. Per-model logic
(02's gates, 03's onset-dating + cross-model classifier, 04's SVAR) does NOT
live here â€” only the shared primitives.

Validated constants (record-keeping; NOT hardcoded into the functions so
callers stay explicit): K=0.5, H=8.0, WARMUP_HOURS=24, corroboration Â±24h.
"""

import numpy as np
import pandas as pd


def deseasonalize(g, signal, time_col="hour", grain="hourly"):
    """Subtract a seasonal median so a recurring pattern isn't read as a step.
    Returns a numpy array aligned to g's rows.

    g:         DataFrame for ONE entity, must contain `time_col` (datetime)
               and `signal`.
    signal:    column name to deseasonalise (e.g. 'win_rate', 'avg_win_price').
    time_col:  name of the datetime column (default 'hour' for Models 01-03).
    grain:     'hourly' — seasonal key = (dayofweek, hour-of-day).  Default;
                          exactly reproduces the v0.1.0 behaviour.
               'daily'  — seasonal key = dayofweek only.  Used by Model 05
                          (headline sensing on daily AAPV × RPM data).
               Any other value raises ValueError (fail fast, no silent fallback).
    """
    if grain not in ("hourly", "daily"):
        raise ValueError(
            f"grain must be 'hourly' or 'daily', got {grain!r}"
        )
    s = g[signal].astype(float)
    ts = g[time_col]
    if grain == "hourly":
        grp_med = s.groupby([ts.dt.dayofweek, ts.dt.hour]).transform("median")
    else:  # daily
        grp_med = s.groupby(ts.dt.dayofweek).transform("median")
    return (s - grp_med).to_numpy()


def robust_scale(x):
    """1.4826*MAD ~ std for normal data; lets CUSUM's K/H be read in sigma units.
    Returns np.nan on empty or zero-spread input (caller must skip those)."""
    x = x[~np.isnan(x)]
    if len(x) == 0:
        return np.nan
    mad = np.median(np.abs(x - np.median(x)))
    scale = 1.4826 * mad
    return scale if scale > 0 else np.nan


def downward_cusum(z, k, h, warmup):
    """One-sided lower CUSUM. Accumulates when z drops below center by > k.
    Fires when S > h. onset = run_start (last index where S was 0 before the run).

    warmup: suppress any fire whose excursion BEGAN in the first `warmup` hours
            (cold-start guard). The guard keys off run_start (where the excursion
            began), NOT fire_idx â€” a real step starting at hour 30 and firing at 32
            still counts; only excursions rooted in the first `warmup` hours die.

    DO NOT "tidy" this by pre-trimming z instead: trimming shifts every onset
    index by `warmup` and corrupts the reported dates. The guard is the correct
    place to handle cold-start.
    """
    S, run_start, peak, armed, events = 0.0, 0, 0.0, True, []
    for t in range(len(z)):
        zt = 0.0 if np.isnan(z[t]) else z[t]   # gap contributes nothing
        S = max(0.0, S - zt - k)
        if S == 0.0:
            run_start, peak, armed = t + 1, 0.0, True   # reset + re-arm
        else:
            peak = max(peak, S)
            if S > h and armed and run_start >= warmup:   # cold-start guard
                events.append({"onset_idx": run_start, "fire_idx": t, "peak": peak})
                armed = False   # one event per excursion until S resets to 0
    return events


def score_series(
    g: pd.DataFrame,
    col: str = "win_rate",
    band_k: int = 5,
    persist: int = 24,
    ewm_halflife: int = 12,
    min_hours: int = 21 * 24,        # need ~3 weeks of history to trust a baseline
):
    """Full leading-layer detector on ONE series (the 01/02 path).

    deseasonalise (dow x hour median) -> EWMA volatility -> auto calm baseline
    -> band = calm_median + band_k*calm_MAD -> alarm if breached >= persist hours.

    The calm baseline is found AUTOMATICALLY (lowest-volatility 7-day window in
    this entity's own history) so each series self-calibrates.

    NOTE (DRY): this inlines its own dow x hour deseasonalise (below). It predates
    the standalone deseasonalize() above. When this function is next touched, that
    inline block should be collapsed into a call to deseasonalize(). Left as-is for
    now: Model 02 is done and trustworthy; don't disturb working code for tidiness.
    """
    g = g.sort_values("hour").set_index("hour")

    if len(g) < min_hours:
        return {"ok": False, "reason": f"only {len(g)}h (<{min_hours})"}

    # 1. Deseasonalise: residual vs (day-of-week x hour-of-day) median  [inline â€” see NOTE]
    idx = g.index
    seas = g.groupby([idx.dayofweek, idx.hour])[col].transform("median")
    g["resid"] = (g[col] - seas) * 100.0

    # 2. EWMA conditional-volatility proxy
    g["vol"] = g["resid"].pow(2).ewm(halflife=ewm_halflife).mean().pow(0.5)

    # 3. AUTO calm baseline: rolling 7d (168h) mean vol, pick the lowest window
    roll = g["vol"].rolling(168, min_periods=168).mean()
    if roll.notna().any():
        calm_end = roll.idxmin()
        calm = g["vol"].loc[:calm_end].iloc[-168:]
    else:
        calm = g["vol"]
    calm_med = calm.median()
    calm_mad = (calm - calm_med).abs().median()
    band = calm_med + band_k * calm_mad

    # 4. Breach -> sustained alarm
    g["breach"] = g["vol"] > band
    g["alarm"] = g["breach"].rolling(persist).sum() >= persist

    alarms = g[g["alarm"]]
    episodes = []
    if len(alarms):
        grp = (alarms.index.to_series().diff() > pd.Timedelta("1h")).cumsum()
        for _, ep in alarms.groupby(grp):
            episodes.append((ep.index.min(), ep.index.max(), len(ep)))

    return {
        "ok": True,
        "vol": g["vol"],
        "band": band,
        "calm_window_end": calm.index.max(),
        "episodes": episodes,
        "currently_alarming": bool(g["alarm"].iloc[-1]),
    }