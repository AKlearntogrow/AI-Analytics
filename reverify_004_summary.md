# Re-verification summary — verifier-0.0.4-check3

- Processed: 76
- Succeeded: 76
- Failed:    0

## Verdict distribution
- PASS_WITH_CAVEATS: 76

## Check 6 (seasonal twin) vote distribution
Prior baseline (0.0.3-check6): SUPPORT 21 / UNDERMINE 47 / NEUTRAL 8
- SUPPORT: 21
- UNDERMINE: 47
- NEUTRAL: 8

## Check 2 (persistence) vote distribution
- NEUTRAL: 3
- SUPPORT: 59
- UNDERMINE: 14

## Check 3 (cross-signal classification) distribution
- COMPOUND: 16
- INSUFFICIENT_DATA: 2
- MONETIZATION: 30
- NO_CROSS_SIGNAL: 10
- VOLUME: 18

## Queue preview — non-seasonal survivors (check 6 != UNDERMINE) — 29

| entity | signal | onset | check-3 classification | est_daily_revenue_delta | check-2 vote |
|---|---|---|---|---:|---|
| Microsoft | rpm | 2024-09-24 | MONETIZATION | -2015.66 | SUPPORT |
| Fandom | rpm | 2025-06-12 | COMPOUND | -1618.71 | SUPPORT |
| Freestar | aapv | 2024-07-04 | VOLUME | -1138.08 | SUPPORT |
| Kleinanzeigen | aapv | 2024-12-18 | COMPOUND | -762.61 | SUPPORT |
| DPGMedia | aapv | 2026-02-27 | VOLUME | -708.75 | SUPPORT |
| Freestar | aapv | 2025-05-24 | VOLUME | -705.89 | SUPPORT |
| Turner-Broadcasting | aapv | 2024-07-26 | COMPOUND | -621.93 | SUPPORT |
| Microsoft | aapv | 2025-04-14 | NO_CROSS_SIGNAL | -561.67 | UNDERMINE |
| Cars.com | rpm | 2025-12-11 | MONETIZATION | -471.08 | SUPPORT |
| DPGMedia-BE | rpm | 2025-05-09 | MONETIZATION | -463.53 | SUPPORT |
| Ookla | rpm | 2025-10-20 | MONETIZATION | -315.09 | SUPPORT |
| Publift | aapv | 2024-02-27 | VOLUME | -306.44 | SUPPORT |
| Kleinanzeigen | rpm | 2025-01-01 | MONETIZATION | -192.21 | SUPPORT |
| Yahoo | aapv | 2024-01-24 | VOLUME | -161.97 | SUPPORT |
| Kleinanzeigen | aapv | 2024-07-05 | NO_CROSS_SIGNAL | -141.13 | UNDERMINE |
| Microsoft | aapv | 2025-09-30 | VOLUME | -108.74 | SUPPORT |
| Kleinanzeigen | aapv | 2025-06-11 | VOLUME | -97.67 | SUPPORT |
| Gannett | rpm | 2026-03-20 | NO_CROSS_SIGNAL | -91.15 | UNDERMINE |
| Orange | aapv | 2024-07-07 | VOLUME | -48.98 | SUPPORT |
| Chess.com | rpm | 2024-02-09 | MONETIZATION | -38.84 | SUPPORT |
| Turner-Broadcasting | aapv | 2026-04-14 | VOLUME | -33.24 | SUPPORT |
| Chess.com | aapv | 2024-06-19 | VOLUME | -7.39 | SUPPORT |
| Microsoft | rpm | 2024-02-09 | MONETIZATION | -7.33 | SUPPORT |
| Publift | aapv | 2026-05-23 | NO_CROSS_SIGNAL | -5.88 | UNDERMINE |
| SlickDeals | aapv | 2026-01-27 | NO_CROSS_SIGNAL | -4.66 | UNDERMINE |
| Valnet | rpm | 2024-08-06 | MONETIZATION | +3.68 | SUPPORT |
| DPGMedia | aapv | 2024-08-27 | VOLUME | +26.22 | SUPPORT |
| Publift | aapv | 2025-05-30 | NO_CROSS_SIGNAL | +751.62 | UNDERMINE |
| Valnet | rpm | 2024-07-11 | INSUFFICIENT_DATA | n/a | NEUTRAL |

## INSUFFICIENT_DATA classifications — 2
- 0e2db12f-27d6-42eb-8b38-6c97d178880c  Valnet  rpm  2024-07-11
- e7e2a6db-7390-4970-8912-1486b3ae4a14  Turner-Broadcasting  rpm  2025-12-15

## Notes on drift from the 0.0.3-check6 baseline

**Check 6**: no drift. SUPPORT 21 / UNDERMINE 47 / NEUTRAL 8 exactly
reproduced. Check 6 behaved deterministically.

**Check 2**: one event drifted SUPPORT (0.0.3-check6) → NEUTRAL (0.0.4-check3):
- Event `32adb4e1-60e0-4d28-94bb-c037fd5177be` — SlickDeals rpm 2024-12-31.
- Cause: transient `RetryError: Timeout of 600.0s exceeded` on the check-2
  BigQuery series query during this run. `_safely` fail-closed caught it and
  recorded NEUTRAL/errored — the intended behaviour under check-2's own
  error contract. Not a semantics change; a re-run of just that event would
  restore SUPPORT (0.0.3 detail was `post_median=6.958 <= 9.755`, well
  inside the persistence band).
- No downstream impact on the queue preview: this event's check 6 =
  UNDERMINE (Jan-cliff cluster), so it's excluded from the survivor table
  above regardless.
- Not retried, because a one-event re-run would append yet another 0.0.4 row
  and muddy the audit trail. Left as-is per append-only policy.

**Check 3**: baseline for future comparison. MONETIZATION 30, VOLUME 18,
COMPOUND 16, NO_CROSS_SIGNAL 10, INSUFFICIENT_DATA 2 = 76.

## Post-run verdicts table state
- verifier-0.0.2-check2: 76
- verifier-0.0.3-check6: 76
- verifier-0.0.4-check3: 76
- **Total: 228 rows**
- Events table: 76 rows, unchanged.
