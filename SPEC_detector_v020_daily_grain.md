# SPEC — detector package v0.2.0: grain-aware deseasonalize

**Repo:** `BetterSaas-engg/AI-Analytics` (installs as distribution `detector`, currently 0.1.0)
**Driver:** Model 05 (headline sensing, AAPV × RPM) runs on DAILY data from
`marble-light.counters.rev_tracker`. Current `deseasonalize` hardcodes an hourly seasonal key
(`dow × hour-of-day`) and a column literally named `hour`. Daily data must be first-class,
not a column-renaming hack.

---

## Change 1 — `deseasonalize` signature (the only behavioral change)

```python
def deseasonalize(g, signal, time_col="hour", grain="hourly"):
```

- `grain="hourly"` → seasonal key = (dayofweek, hour) of `g[time_col]`  — EXACTLY current behavior
- `grain="daily"`  → seasonal key = dayofweek of `g[time_col]`
- `time_col` default `"hour"` — Models 01–03 call sites keep working with zero edits
- Raise `ValueError` on any other grain value (fail fast, no silent fallback)
- Docstring updated to describe both grains and the Model 05 use case

**Backwards compatibility is a hard requirement:** existing calls
`deseasonalize(g, signal)` must return bit-identical output to 0.1.0.

## Change 2 — version bump

`pyproject.toml` version 0.1.0 → 0.2.0. Nothing else in the package changes —
`downward_cusum`, `robust_scale`, `score_series` are frozen (warmup guard semantics
in `downward_cusum` must NOT be touched: guard keys off `run_start >= warmup`,
never pre-trims the series).

## Change 3 — tests (prove, don't assume)

1. **Regression:** synthetic hourly frame → 0.2.0 default-args output equals the
   0.1.0 implementation's output exactly.
2. **Daily grain:** synthetic daily frame with a known day-of-week pattern
   (e.g. weekends 20% lower) → deseasonalized output has the weekend dip removed
   (dow-group medians ≈ 0).
3. **Fail fast:** `grain="weekly"` raises ValueError.

## Out of scope — explicitly

- No changes to the other three functions.
- No day-of-month seasonality (month-start pattern) — noted as a possible v0.3
  after Model 05 shows whether dow-only leaves month-start residue.
- Model 01–03 workbooks are NOT edited in this session. (Known issue, tracked
  separately: Model 03's workbook contains an inline drifted copy of
  `downward_cusum` missing the warmup guard — workbook cleanup is its own task.)

## Definition of done

- `pip install git+...` in a fresh env → `from detector import deseasonalize` →
  daily test passes.
- Tag/commit message references this spec.
