# SPEC — Verifier check 6: seasonal twin

## Goal
Add check 6 to verify_event(): does this drop have a "twin" at the same time
of year in prior years? A recurring annual drop is expected seasonality, not
an incident. Acceptance test: the January-cliff cluster (Dec 21–Jan 2 RPM
onsets across many orgs, repeating yearly) should come back UNDERMINE.
Checks 3–5 and 7 stay NOT_IMPLEMENTED.

## Definition (constants at top, benchmark will tune)
- TWIN_LOOKBACK_YEARS = 2
- TWIN_WINDOW_TOLERANCE_DAYS = 10  (twin onset may sit ±10 days from the
  same calendar date in a prior year)
- Reuse check 2's constants: BASELINE_DAYS=28, POST_DAYS=14,
  PERSISTENCE_THRESHOLD=0.10, MIN_POST_DAYS=7 — and its per-signal series
  SQL (aapv = SUM(aa_pageviews); rpm = sums-then-divide). Refactor the
  series-fetch into a shared helper rather than duplicating SQL (DRY).

Logic per prior year y in 1..TWIN_LOOKBACK_YEARS:
1. twin_onset = event onset date minus y years (calendar date;
   Feb-29 edge → Feb-28).
2. Pull the daily series for [twin_onset - BASELINE_DAYS - TOLERANCE,
   twin_onset + POST_DAYS + TOLERANCE].
3. Slide candidate onsets across twin_onset ± TOLERANCE days; for each,
   compute baseline_median (28d before) and post_median (14d after).
4. A twin EXISTS in year y if any candidate onset shows
   post_median <= baseline_median * (1 - PERSISTENCE_THRESHOLD)
   with >= MIN_POST_DAYS of post data and nonzero baseline.

Check result:
- Twin exists in >= 1 prior year → UNDERMINE
  (detail: which year(s), best candidate onset date, ratio).
- No twin in any prior year that HAD sufficient data → SUPPORT
  (this drop is not an annual repeat).
- No prior year had sufficient data → NEUTRAL, flagged.

## Regime caveat (record, don't block)
Seasonal baselines in detection learn 2024-01-01+ only, but the handoff
allows older history as context — twin lookback is exactly context use.
When a twin match relies on pre-2024 data, include "pre_regime": true in
that year's detail so the benchmark can judge whether pre-regime twins
should count.

## Verdict wiring
Check 6 votes like check 2 (SUPPORT/UNDERMINE/NEUTRAL) in the existing
general verdict rule. Note: with checks 1+2+6 live, the KILL branch
(>=2 UNDERMINE + 0 SUPPORT) is still inert while check 1 counts as
SUPPORT — leave as-is, it's already a BENCHMARK REVIEW item.
Errored check 6 → NEUTRAL flagged, intended SQL recorded with
"errored": true (same pattern as check 2).

## Constraints (unchanged)
Read-only vs source data; parameterized SQL; only write = verdicts append.

## Tests (mocked BQ client)
- UNDERMINE: prior-year series with a clear matching drop at same calendar
  position.
- UNDERMINE via tolerance: twin drop offset by 8 days still matches.
- SUPPORT: prior years have data but no comparable drop.
- NEUTRAL: no prior-year data (e.g. 2024 fire with lookback before data
  starts); errored query.
- pre_regime flag set when twin year < 2024.
- Shared series helper: check 2 and check 6 produce identical SQL for the
  same window (regression: check 2's existing tests still green after
  refactor).

## Out of scope
Checks 3–5, 7. Brief generator. Re-running verdicts over the 76 events
(that's a follow-up decision — verdicts are append-only, a rerun writes
new rows under a bumped config_version). Package version stays 0.3.x;
config_version → "verifier-0.0.3-check6".

## Working style
Implement, pause for review before commit. Flag judgment calls.
