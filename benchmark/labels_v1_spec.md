# Benchmark label definition — Watchtower 2.0 verifier
Written 2026-07-20, before any labelling. Frozen: changes require a new
version of this file, not an edit, so that labels stay traceable to the
definition in force when they were made.

## What a label answers
For each event, one question only:

  **Did a real, material change occur at this onset that a publisher-facing
  person should be told about?**

Not "is this statistically significant." Not "did the detector behave
correctly." Not "is this seasonal." Whether it warrants a human reaching out.

## Label values
- **TRUE** — a real, material change occurred and it warrants contact.
- **FALSE** — no real change (noise, artifact), OR a real change that does
  not warrant contact (routine, expected, immaterial).
- **UNSURE** — cannot tell from the series shown.

UNSURE is a first-class answer. Forcing a call on an ambiguous case
manufactures false confidence. Expect 10-20% UNSURE; if it is near zero,
suspect over-confidence rather than clarity.

## Decision guidance
Mark TRUE when the post-onset level is visibly and sustainedly different from
the pre-onset level, and the size of the change is large enough that a
publisher would care.

Mark FALSE when:
- the series returns to its prior level within the visible window (transient),
- the change is within the series' normal wobble,
- the shift is visible but small enough to be commercially irrelevant,
- the pattern is obviously a data artifact (a zero-floor, a single spike, a
  gap and resume at the same level).

Mark UNSURE when the series is too noisy, too short, or too gappy to judge.

## Seasonality — deliberately excluded
Do NOT consider whether a change looks seasonal or recurring. You will not be
shown prior years. Seasonality is check 6's job; if you factor it in, you are
grading check 6 against itself and the benchmark becomes circular.

An annual January drop should be labelled TRUE if it is real and material.
Check 6 UNDERMINE-ing it is then a correct *routing* decision, scored
separately, not a correctness failure of the label.

## Blinding rules (binding on the sampling script)
The labeller sees ONLY:
- the NORMALIZED series for the event's own signal, plotted, covering
  roughly 90 days before to 60 days after onset. Normalization: each series
  is indexed to 100 at the baseline mean (mean of the 28 days pre-onset);
  the y-axis shows the index, not absolute units.
- the onset date marked on the plot,
- which signal it is (aapv or rpm) — needed to set expectations about
  volatility, not scale,
- an opaque label ID.

Rationale for normalization: absolute scale leaks entity identity (a
publisher labeller can recognize a known scale range), and both labellers
must see identical stimuli or inter-rater agreement is meaningless. The
normalized index also puts every plot on the same visual footing so
"visibly and sustainedly different" is judged against a consistent yardstick.

The labeller must NOT see:
- entity/publisher name,
- the verdict, any check vote, or check 3's classification,
- the estimated revenue delta,
- whether the event was a check-6 survivor or UNDERMINE,
- any prior year of data,
- absolute y-axis units.

Label IDs are shuffled so survivors and UNDERMINEs are indistinguishable by
position.

## Recency rule
`MIN_POST_DAYS_FOR_LABEL = 30`. If an event has fewer than 30 days of
post-onset data available at sampling time, the sampling script warns and
marks the item as UNSURE-eligible rather than rendering a short plot as if
it were complete. A short plot pressures the labeller toward FALSE (nothing
persistent visible yet) when the honest answer is that there isn't enough
post-onset data to judge.

No current event in the sample trips this rule; the earliest onset with the
least post-onset data (Publift aapv 2026-05-23) has ~58 post-onset days.
The rule is for future batches.

## Procedure
- Label in one or two sittings; stop when tired. Fatigued labels are worse
  than missing labels.
- Do not revisit earlier labels after seeing later ones.
- Record a one-line reason for every label. The reasons are as valuable as
  the labels when a disagreement gets adjudicated later.
- Record UNSURE freely rather than guessing.

## Second labeller
Graham labels 15 events drawn from the SAME 44 sample (10 survivors, 5
UNDERMINEs), not an additional 15. He sees the same blinded plots and
label IDs; only the 15 overlap events are on his sheet. Agreement on that
overlap sets the ceiling on any score the verifier can achieve. Report
raw agreement and Cohen's kappa before reporting any verifier metric.

**Kappa at n=15 is directional only** — treat it as "the two labellers
appear to agree well / poorly / not at all" rather than a precise figure.
The confidence interval on kappa at that sample size is wide enough that
±0.2 shifts are within noise. Do not tune anything against a specific
kappa value; use it only to decide whether the definition itself needs
revision.

If agreement is poor (kappa directionally < ~0.4), the definition above is
the problem, not the verifier. Revise this document to a v2, re-label,
and do not tune thresholds against v1 labels.

## Sample composition
- All 29 check-6 survivors (SUPPORT or NEUTRAL).
- 15 randomly sampled from the 47 check-6 UNDERMINEs, using a fixed random
  seed recorded in the sampling script.
- Total 44, shuffled, blinded.

Rationale: survivors alone measure precision only. The 15 killed events are
the only way to detect check 6 over-killing real problems.

## Storage
Labels are written to `benchmark/labels_v1.csv` with columns:
label_id, event_id, label, reason, labeller, labelled_at.

- The **reason** column is mandatory and non-empty; the labelling tooling
  must reject a row with a blank reason. Reasons drive adjudication when
  labellers disagree and are the only durable record of what the labeller
  actually saw.
- Committed to the repo.
- Immutable once complete — corrections go in a v2 file with a note
  explaining what changed and why.
