# AI-Analytics — Detector Engine

Shared engine for the operational-intelligence portfolio (Blockthrough programmatic
ad business). Models *sense* and *explain* drivers of `Revenue = AAPV x RPM / 1000`.
This repo holds ONLY the shared engine. Per-model logic stays in the Hex notebooks.

## What lives here
`src/detector/engine.py` — four frozen primitives, diff-and-reconciled from the
Model 01-03 notebooks:
- `deseasonalize(g, signal)` — dow x hour median subtract -> np array
- `robust_scale(x)` — 1.4826*MAD; NaN on flat/empty
- `downward_cusum(z, k, h, warmup)` — one-sided lower CUSUM, cold-start guarded
- `score_series(g, col, ...)` — EWMA-vol leading detector (01/02 path)

Validated constants (callers pass explicitly; NOT hardcoded):
`K=0.5, H=8.0, WARMUP_HOURS=24`, corroboration tolerance `±24h`.

## Hard rules (these encode bugs we already reasoned through — do not "tidy" away)
1. `downward_cusum` warmup is a GUARD on `run_start`, never pre-trimming of `z`.
   Pre-trimming shifts every onset index by `warmup` and corrupts reported dates.
2. `score_series` inlines its own deseasonalise. It predates `deseasonalize()`.
   Collapse the inline into a call to `deseasonalize()` ONLY when this function is
   next touched for a real reason. Do not refactor working code for tidiness alone.
3. Models 02 and 03 notebooks are DONE and trustworthy. Migrate their logic into
   this repo on-touch only (e.g. CUSUM-v2 = the deferred Mid effect-size gate).

## How notebooks consume this
Hex (cloud) installs from GitHub:
    pip install git+https://github.com/AKlearntogrow/AI-Analytics.git
    from detector import deseasonalize, robust_scale, downward_cusum, score_series

## Python target
`requires-python = ">=3.10"` — floor for Hex runtime compatibility, NOT local 3.14.
