# SPEC — Verifier check 2: persistence

## Goal
Add check 2 to verify_event(): did the drop persist after onset, or recover?
Wire it into the verdict logic alongside check 1. Checks 3–7 stay NOT_IMPLEMENTED.

## Definition (constants at top of module, benchmark will tune)
- BASELINE_DAYS = 28 (before onset)
- POST_DAYS = 14 (after onset, or onset→today if recent)
- PERSISTENCE_THRESHOLD = 0.10
- MIN_POST_DAYS = 7

Signal series per org from `marble-light.counters.rev_tracker`
(entity col = orgName, date col = date, aggregated per day across websites):
- aapv: SUM(aa_pageviews)
- rpm: SAFE_DIVIDE(SUM(ProgRev) + SUM(DirectRev), SUM(aa_pageviews)) * 1000
  (NULLIF guard on zero AAPV; sum revenue and pageviews FIRST, then divide —
  never average per-website RPMs)

Logic:
1. One parameterized query pulls the daily series for
   [onset - 28d, onset + 14d] for the event's entity + signal.
2. baseline_median = median over days strictly before onset.
3. post_median = median over days from onset onward.
4. Fewer than MIN_POST_DAYS post-onset days with data → status NEUTRAL,
   detail "insufficient post-onset data (n=X)".
5. post_median <= baseline_median * (1 - PERSISTENCE_THRESHOLD) → SUPPORT.
6. Otherwise → UNDERMINE (recovered / transient).
7. baseline_median is 0 or NULL → NEUTRAL, flagged (can't assess).

## Verdict logic update
Contract rule now becomes live: check 1 FAIL → KILL (unchanged).
Otherwise: ≥2 UNDERMINE and 0 SUPPORT → KILL. With only checks 1–2
implemented this KILL branch can't trigger yet (max 1 UNDERMINE) — implement
the rule generally, not hardcoded to check count.
Any implemented check NEUTRAL or UNDERMINE → PASS_WITH_CAVEATS with the
detail in caveats. All implemented checks SUPPORT/PASS → still
PASS_WITH_CAVEATS while any checks remain NOT_IMPLEMENTED.

## Constraints (unchanged from tracer)
- Read-only against source data; only write = verdicts append.
- Parameterized SQL only. Errored check → NEUTRAL flagged (fail closed) —
  now implement this properly for check 2 (try/except around the check,
  never let a check exception kill the verdict write).
- evidence_refs gains the check-2 SQL text alongside check 1's.

## Tests (mocked BQ client)
- SUPPORT: post clearly below baseline beyond threshold.
- UNDERMINE: full recovery.
- NEUTRAL: <7 post days; zero baseline; check-2 query raises.
- Boundary: post_median exactly at baseline*(1-0.10) → SUPPORT (<=).
- RPM aggregation: sums-then-divide, not average-of-ratios.
- Verdict wiring: check1 PASS + check2 SUPPORT → PASS_WITH_CAVEATS;
  check1 PASS + check2 UNDERMINE → PASS_WITH_CAVEATS with caveat;
  check1 FAIL → KILL regardless of check 2.

## Out of scope
Checks 3–7, brief generator, any re-verification of already-verdicted events,
version bump (stays 0.3.x until checks batch meaningfully).
