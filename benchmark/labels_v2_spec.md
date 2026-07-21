# Benchmark label definition — Watchtower 2.0 verifier — v2

**SUPERSEDES:** `benchmark/labels_v1_spec.md` as of 2026-07-20. v1 stays
in git as the frozen historical record; v2 governs all labels made under
files matching `labels_v2*.csv`. Reason for v2: v1 asked the labeller to
judge commercial materiality from a blinded normalized chart with no
publisher, no units, and no revenue — a question the chart cannot answer,
and one that would force each labeller to invent a private threshold
that destroys inter-rater agreement. v2 narrows the label to a purely
observational question (was the level change real and persistent?),
delegates materiality to `est_daily_revenue_delta` downstream, clarifies
UNSURE, and adds guidance that the onset date is an estimate, not a step.

Written 2026-07-20, before any v2 labelling. Frozen: changes require a
new version of this file, not an edit, so that labels stay traceable to
the definition in force when they were made.

## What a label answers
For each event, one question only:

  **Did a real change in level occur at or near the onset date, and did
  it persist through the visible window?**

Not "is this statistically significant." Not "did the detector behave
correctly." Not "is this seasonal." Not "is this commercially material" —
the system handles that downstream via `est_daily_revenue_delta`.

Division of labour: the human judges whether the change is REAL; the
system judges whether it is BIG (from absolute units the labeller does
not see). Blinding intentionally removes the labeller's ability to judge
materiality, and asking them to do so anyway forces private thresholds
that destroy inter-rater agreement.

## Label values
- **TRUE** — a real change in level occurred at or near the onset date,
  and it persisted through the visible window.
- **FALSE** — no real change in level (noise, artifact, single spike),
  OR the level dropped and returned to its prior band within the window.
- **UNSURE** — the series does not support a call.

UNSURE is a first-class answer. Forcing a call on an ambiguous case
manufactures false confidence. Expect 10-20% UNSURE; if it is near zero,
suspect over-confidence rather than clarity.

## Decision guidance
Mark TRUE when the post-onset level is visibly and sustainedly different
from the pre-onset level. Size of the change is not your call — that is
downstream. Judge only whether the level shifted and stayed shifted.

Mark FALSE when:
- the series returns to its prior level within the visible window
  (transient),
- the change is within the series' normal wobble,
- the pattern is obviously a data artifact (a zero-floor, a single
  spike, a gap and resume at the same level).

Mark UNSURE when the data will not support a call — too noisy, too
gappy, or a shift smaller than the series' own weekly swing. UNSURE is
**NOT** for cases where the answer is clear but feels uncomfortable. If
you can describe what happened, you can label it.

## The onset date is approximate
The red line is the detector's ESTIMATE of when the change began. In
practice the decline often starts two to four weeks earlier and runs
through day 0 as a continuous slide rather than a step. **Do not mark
FALSE because there is no clean step at the line.** Judge whether the
level before the window differs from the level after it. If the slide
begins well before onset, label the change and note the timing in your
reason.

## Seasonality — deliberately excluded
Do NOT consider whether a change looks seasonal or recurring. You will
not be shown prior years. Seasonality is check 6's job; if you factor it
in, you are grading check 6 against itself and the benchmark becomes
circular.

An annual January drop should be labelled TRUE if it is a real
persistent change in level. Check 6 UNDERMINE-ing it is then a correct
*routing* decision, scored separately, not a correctness failure of the
label.

## Blinding rules (binding on the sampling script)
The labeller sees ONLY:
- the NORMALIZED series for the event's own signal, plotted, covering
  roughly 90 days before to 60 days after onset. Normalization: each
  series is indexed to 100 at the baseline mean (mean of the 28 days
  pre-onset); the y-axis shows the index, not absolute units.
- the onset date marked on the plot,
- which signal it is (aapv or rpm) — needed to set expectations about
  volatility, not scale,
- an opaque label ID.

Rationale for normalization: absolute scale leaks entity identity (a
publisher labeller can recognize a known scale range), and both
labellers must see identical stimuli or inter-rater agreement is
meaningless. The normalized index also puts every plot on the same
visual footing so "visibly and sustainedly different" is judged against
a consistent yardstick.

The labeller must NOT see:
- entity/publisher name,
- the verdict, any check vote, or check 3's classification,
- the estimated revenue delta,
- whether the event was a check-6 survivor or UNDERMINE,
- any prior year of data,
- absolute y-axis units.

Label IDs are shuffled so survivors and UNDERMINEs are indistinguishable
by position.

## Recency rule
`MIN_POST_DAYS_FOR_LABEL = 30`. If an event has fewer than 30 days of
post-onset data available at sampling time, the sampling script warns
and marks the item as UNSURE-eligible rather than rendering a short plot
as if it were complete. A short plot pressures the labeller toward
FALSE (nothing persistent visible yet) when the honest answer is that
there isn't enough post-onset data to judge.

No current event in the sample trips this rule; the earliest onset with
the least post-onset data (Publift aapv 2026-05-23) has ~58 post-onset
days. The rule is for future batches.

## Procedure
- Label in one or two sittings; stop when tired. Fatigued labels are
  worse than missing labels.
- Do not revisit earlier labels after seeing later ones.
- Record a one-line reason for every label. The reasons are as valuable
  as the labels when a disagreement gets adjudicated later.
- Record UNSURE freely rather than guessing.

## Labellers
**Primary labeller (AK)**: labels 43 of the 44 events. **L001 is EXCLUDED**
for AK — he has already seen the underlying series during earlier work on
that event — and must be labelled by the second labeller only.

**Second labeller (SE, a solution engineer)**: labels 15 of the 44 events,
drawn as 10 survivors + 5 UNDERMINEs using the existing sampling seed.
**L001 must be one of the 15.** If the seeded selection does not already
contain L001, swap it in and drop one other survivor (the alphabetically-
last survivor in the current selection) to keep the 10/5 stratum split
intact.

Agreement between AK and SE is computed on those 15 overlapping events.
It sets the ceiling on any score the verifier can achieve. Report raw
agreement and Cohen's kappa before reporting any verifier metric.

**Kappa at n=15 is directional only** — treat it as "the two labellers
appear to agree well / poorly / not at all" rather than a precise
figure. The confidence interval on kappa at that sample size is wide
enough that ±0.2 shifts are within noise. Do not tune anything against a
specific kappa value; use it only to decide whether the definition
itself needs revision.

If agreement is poor (kappa directionally < ~0.4), the definition above
is the problem, not the verifier. Revise this document to a v3, re-label,
and do not tune thresholds against v2 labels.

## Sample composition
- All 29 check-6 survivors (SUPPORT or NEUTRAL).
- 15 randomly sampled from the 47 check-6 UNDERMINEs, using a fixed
  random seed recorded in the sampling script.
- Total 44, shuffled, blinded.

Rationale: survivors alone measure precision only. The 15 killed events
are the only way to detect check 6 over-killing real problems.

## Storage
Labels are written to `benchmark/labels_v2.csv` with columns:
label_id, event_id, label, reason, labeller, labelled_at.

- The **reason** column is mandatory and non-empty; the labelling
  tooling must reject a row with a blank reason. Reasons drive
  adjudication when labellers disagree and are the only durable record
  of what the labeller actually saw.
- Committed to the repo.
- Immutable once complete — corrections go in a v3 file with a note
  explaining what changed and why.
