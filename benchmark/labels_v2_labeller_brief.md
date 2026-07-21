# Labelling charts — quick brief

> **USER-FACING COPY.** This is the plain-English brief that goes to each
> labeller alongside their zip. The frozen technical definition of what a
> label means lives in `benchmark/labels_v2_spec.md` — keep both in sync
> if either changes.

Should take about 30–45 minutes. Read this, do the worked example, then go.

---

## What we need from you

We have a system that flags when a publisher's traffic or revenue drops.
It flags more than we can act on, and we don't know how good its
judgement is.

So we're asking a human to look at the same data and call it
independently. Where you and the system disagree, we investigate. Your
answers become the reference we tune everything against.

You get **24 charts**. For each one, one question:

> **Did a real change in level happen at the marked date, and did it stick?**

Three answers: TRUE, FALSE, UNSURE.

---

## Where this sits

Six steps. Yours is step 4.

1. **Collect** — daily traffic and revenue per publisher.
2. **Detect** — models scan for sudden sustained drops and flag them.
3. **Verify** — automated checks ask: is the data real, did the drop
   stick, is it a traffic or a money problem, does it happen every year?
4. **Judge — you.** 24 of those flagged drops, with the system's opinion
   hidden, judged by a human.
5. **Score** — your answers vs the system's. Disagreements tell us what
   to fix.
6. **Ship** — a queue our team works through: "these publishers need a
   call this week."

**Why it matters:** everything downstream is tuned against your labels.
If the system is over-flagging, we're wasting the team's time; if it's
under-flagging, we're missing real revenue problems. Right now we can't
tell which — your 24 charts are how we find out.

The system's opinion is hidden on purpose. If you could see it, you'd
unconsciously agree with it, and we'd learn nothing.

---

## Reading a chart

The charts are deliberately stripped down — no publisher name, no dates,
no dollars. That's on purpose (see "Two rules" below).

- **X-axis**: days relative to the flagged date. Day 0 is the red line.
- **Y-axis**: an index. The average of the 28 days before the red line
  is set to 100. So 80 means "20% below where it had been."
- **Gaps** in the line mean missing data — not zero.

Four moves, in order:

1. **Find the weekly zigzag.** These series rise and fall every 7 days
   (weekday vs weekend). That's noise. Note roughly how big the swing is
   — that's your noise floor.
2. **Find the band.** Ignore the zigzag; imagine a smooth line halfway
   between the tops and the bottoms. Where does it sit before the red
   line? Where after?
3. **Did it come back?** Look at the far right end. Back to the old
   level, or still down?
4. **Sanity-check the 100 line.** It's only the average of the 28 days
   before onset. If that stretch was unusually high or low, 100 is
   misleading. Glance at the far left to see the series' normal level.

---

## Worked example

Open `P008.png` from your zip and follow along. (This is a practice
chart, not one of your 24.)

- **Move 1 — the zigzag.** Clear weekly pattern, swinging about 20
  points top to bottom. That's the noise floor.
- **Move 2 — the band.** Before the red line, the middle of the band
  sits around 120. After, around 70. A 50-point drop — far bigger than
  the 20-point noise.
- **Move 3 — did it come back?** No. It stays around 70 for the full
  60 days.
- **Move 4 — the 100 line.** Fine here; the series' normal level is
  genuinely above 100 earlier in the window, which means the real drop
  is if anything bigger than the 100 line suggests.

One thing to notice: there's no clean step at the red line. The decline
starts around day -30 and slides down through onset. That's normal and
it's still TRUE — the red line is the system's *estimate* of when things
started, and it's often late. Judge the level before versus after, not
whether there's a sharp break.

**Call: TRUE**
**Reason:** *"level falls ~120 → ~70 with no recovery through +60;
decline begins ~day -30, so onset is approximate."*

Once you've made your own call for P008, open `practice_ANSWERS.csv` to
see what the system said. That's just calibration — no wrong answer.

---

## What each answer means (reminder)

- **TRUE** — the level clearly moved and stayed moved through the end of
  the window.
- **FALSE** — the level didn't really move; or it dropped and came back
  to roughly where it started. A deep dive that recovers is still FALSE
  — what matters is where it ends up, not how bad it got in the middle.
- **UNSURE** — the data won't support a call: too noisy, big gaps, or
  the shift is smaller than the weekly swing.

UNSURE is a real answer, not a cop-out — but only use it when you
genuinely can't tell. If you can *describe* what happened ("it dropped
and came back"), you can label it.

---

## Two rules that will feel wrong

1. **Ignore seasonality.** Some drops will look like the usual January
   or summer pattern. Label them TRUE anyway if they're real and
   sustained. A separate part of the system decides whether seasonal
   drops reach the queue — and that part is exactly what we're testing.
   If you filter them out yourself, we're grading it against itself.
   This is also why there are no calendar dates on the charts.
2. **Ignore how big it is in money terms.** You can't see dollars, and
   that's deliberate. Judge whether the change is *real*. The system
   ranks by size afterwards.

---

## Filling it in

`label_id` and `signal` are already filled. You add four columns:

| label_id | signal | label  | reason                                                              | labeller  | labelled_at |
| -------- | ------ | ------ | ------------------------------------------------------------------- | --------- | ----------- |
| L001     | aapv   | TRUE   | level falls ~110 → ~85, no recovery through +60                     | Your Name | 2026-07-21  |
| L006     | rpm    | FALSE  | dips to ~60 at onset, back to the ~100 band by +12; transient       | Your Name | 2026-07-21  |
| L012     | aapv   | UNSURE | shift ~8 points against a weekly swing of ~25; can't call it        | Your Name | 2026-07-21  |

*(Illustrative rows — not real answers.)*

- `label` must be exactly `TRUE`, `FALSE`, or `UNSURE`.
- `reason` can't be blank. One line: level before, level after, whether
  it came back.
- Fill `labeller` and `labelled_at` down every row first, then work the
  charts.

---

## Housekeeping

- Don't go back and revise earlier labels after seeing later ones.
- Stop if you get tired — two sittings is fine.
- Ask me anything about the method. Please don't ask about a specific
  chart — once we've discussed one, that label is compromised.
