"""scripts/sample_benchmark_v1.py — build the blinded v1 labelling package.

Binding definition: benchmark/labels_v1_spec.md. This script must not
contradict it. Not package code; not scored.

Produces:
  - benchmark/labels_v1_TEMPLATE.csv       (the labeller opens THIS)
  - benchmark/labels_v1_KEY.csv            (do NOT open until labelling done)
  - benchmark/labels_v1_graham_TEMPLATE.csv
  - benchmark/plots_v1/L001.png .. L044.png

Determinism: RANDOM_SEED = 20260720 drives, in this fixed order:
  1. sampling 15 killed events from 47,
  2. shuffling the combined 44 to assign L001..L044,
  3. picking Graham's 10-survivor + 5-killed subset from the 44.
Re-running with the same seed produces identical label_id -> event_id
assignment and identical Graham subset. tests/test_sample_benchmark_v1.py
pins this.

Schema note vs spec §Storage: the spec lists labels_v1.csv columns as
(label_id, event_id, label, reason, labeller, labelled_at). The TEMPLATE
the labeller opens DELIBERATELY EXCLUDES event_id — event_id trivially
traces back to entity via the events table, which the spec's blinding
rules forbid. Post-labelling, TEMPLATE + KEY join on label_id to
reconstruct the spec's stated schema.

Usage:
  python scripts/sample_benchmark_v1.py --dry-run   # composition only
  python scripts/sample_benchmark_v1.py             # write files + plots
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Reuse the verifier's helpers so we DO NOT write a second series SQL.
from detector.verifier import (
    BASELINE_DAYS,
    _default_client,
    _fetch_series,
    _series_sql_for,
)


# ---- Constants (all sample selection depends on these + RANDOM_SEED) ----
RANDOM_SEED = 20260720
CONFIG_VERSION = "verifier-0.0.4-check3"
# Frozen sampling date — the day the v1 sample was chosen. Recorded in
# the KEY header so re-rendering plots later doesn't rewrite history.
# The recency guard still uses date.today() — that IS about "right now".
SAMPLING_DATE = date(2026, 7, 20)
EXPECTED_SURVIVORS = 29
EXPECTED_KILLED = 47
N_KILLED_SAMPLE = 15
N_TOTAL = EXPECTED_SURVIVORS + N_KILLED_SAMPLE  # 44
GRAHAM_N_SURVIVORS = 10
GRAHAM_N_KILLED = 5
MIN_POST_DAYS_FOR_LABEL = 30
PLOT_PRE_DAYS = 90
PLOT_POST_DAYS = 60

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_DIR = REPO_ROOT / "benchmark"
PLOTS_DIR = BENCHMARK_DIR / "plots_v1"
SMOKE_DIR = BENCHMARK_DIR / "plots_smoketest"
TEMPLATE_PATH = BENCHMARK_DIR / "labels_v1_TEMPLATE.csv"
KEY_PATH = BENCHMARK_DIR / "labels_v1_KEY.csv"
GRAHAM_PATH = BENCHMARK_DIR / "labels_v1_graham_TEMPLATE.csv"

_VALID_LABELS = {"TRUE", "FALSE", "UNSURE"}


# ---------------------------------------------------------------------------
# BigQuery fetch
# ---------------------------------------------------------------------------

def _fetch_latest_verdicts(bq) -> list[dict]:
    """Return [{event_id, check_6_status}] — latest verdict per event
    under CONFIG_VERSION (by verdict_ts). Deduplicates any accidental
    re-runs; consumers of the verdicts table read latest-per-event."""
    from google.cloud import bigquery

    sql = """
    WITH ranked AS (
      SELECT event_id, check_results,
             ROW_NUMBER() OVER (
               PARTITION BY event_id ORDER BY verdict_ts DESC
             ) AS rn
      FROM `marble-light.operational_intelligence.verdicts`
      WHERE config_version = @cv
    )
    SELECT event_id, check_results FROM ranked WHERE rn = 1
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("cv", "STRING", CONFIG_VERSION),
    ])
    out: list[dict] = []
    for r in bq.query(sql, job_config=job_config).result():
        cr = r["check_results"]
        if isinstance(cr, str):
            cr = json.loads(cr)
        out.append({
            "event_id": r["event_id"],
            "check_6_status": cr["check_6"]["status"],
        })
    return out


def _fetch_events_meta(bq, event_ids) -> dict:
    """Return {event_id: {entity, signal, onset_ts}}. onset_ts is
    tz-aware (UTC)."""
    from google.cloud import bigquery

    sql = """
    SELECT event_id, entity, signal, onset_ts
    FROM `marble-light.operational_intelligence.events`
    WHERE event_id IN UNNEST(@ids)
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("ids", "STRING", list(event_ids)),
    ])
    out: dict = {}
    for r in bq.query(sql, job_config=job_config).result():
        onset = r["onset_ts"]
        if onset.tzinfo is None:
            onset = onset.replace(tzinfo=timezone.utc)
        out[r["event_id"]] = {
            "entity": r["entity"],
            "signal": r["signal"],
            "onset_ts": onset,
        }
    return out


# ---------------------------------------------------------------------------
# Pure sampling logic (deterministic; tested)
# ---------------------------------------------------------------------------

def _stratify(latest_verdicts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split by check_6 status. Fails loudly on any unexpected status."""
    survivors = [r for r in latest_verdicts
                 if r["check_6_status"] in ("SUPPORT", "NEUTRAL")]
    killed = [r for r in latest_verdicts
              if r["check_6_status"] == "UNDERMINE"]
    other = [r for r in latest_verdicts
             if r["check_6_status"] not in ("SUPPORT", "NEUTRAL", "UNDERMINE")]
    if other:
        raise SystemExit(f"unexpected check_6 statuses in verdicts: {other}")
    return survivors, killed


def _assign_sample(survivors, killed, rng) -> list[dict]:
    """Sample 15 killed, combine with all 29 survivors, shuffle, and
    assign L001..L044. Deterministic given rng state on entry.

    Returned dicts carry: event_id, stratum, check_6_status, label_id.
    """
    if len(survivors) != EXPECTED_SURVIVORS:
        raise ValueError(
            f"expected {EXPECTED_SURVIVORS} survivors, got {len(survivors)}"
        )
    if len(killed) != EXPECTED_KILLED:
        raise ValueError(
            f"expected {EXPECTED_KILLED} killed, got {len(killed)}"
        )

    # Deterministic input ordering — output then depends only on rng.
    survivors_sorted = sorted(survivors, key=lambda r: r["event_id"])
    killed_sorted = sorted(killed, key=lambda r: r["event_id"])

    sampled_killed = rng.sample(killed_sorted, N_KILLED_SAMPLE)

    combined = (
        [{"event_id": r["event_id"], "stratum": "survivor",
          "check_6_status": r["check_6_status"]}
         for r in survivors_sorted]
        + [{"event_id": r["event_id"], "stratum": "killed",
            "check_6_status": r["check_6_status"]}
           for r in sampled_killed]
    )
    rng.shuffle(combined)
    for i, item in enumerate(combined, start=1):
        item["label_id"] = f"L{i:03d}"
    return combined


def _pick_graham_subset(assigned, rng) -> list[str]:
    """From the 44 assigned items, pick 10 survivor label_ids + 5 killed
    label_ids using rng. Returns a sorted list of label_ids."""
    survivor_ids = sorted(r["label_id"] for r in assigned
                          if r["stratum"] == "survivor")
    killed_ids = sorted(r["label_id"] for r in assigned
                        if r["stratum"] == "killed")
    picks = (
        rng.sample(survivor_ids, GRAHAM_N_SURVIVORS)
        + rng.sample(killed_ids, GRAHAM_N_KILLED)
    )
    return sorted(picks)


# ---------------------------------------------------------------------------
# Series → normalized plot
# ---------------------------------------------------------------------------

def _compute_post_days(onset_ts: datetime, today: date) -> int:
    """Elapsed calendar days from onset to `today`, capped at PLOT_POST_DAYS."""
    onset_day = onset_ts.date()
    elapsed = (today - onset_day).days
    return max(0, min(elapsed, PLOT_POST_DAYS))


def _normalize(rows, onset_day: date) -> tuple[float | None, int]:
    """Baseline mean over [onset - BASELINE_DAYS, onset). Nones dropped."""
    baseline_vals = [
        float(r["value"]) for r in rows
        if (onset_day - timedelta(days=BASELINE_DAYS)) <= r["d"] < onset_day
        and r["value"] is not None
    ]
    if not baseline_vals:
        return None, 0
    return sum(baseline_vals) / len(baseline_vals), len(baseline_vals)


def _render_plot(label_id, signal, rows, onset_day, out_path, baseline_mean):
    """Normalized plot. Gaps render as gaps (NaN breaks the line)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    day_to_value = {r["d"]: r["value"] for r in rows}
    xs = list(range(-PLOT_PRE_DAYS, PLOT_POST_DAYS + 1))
    ys = []
    for x in xs:
        d = onset_day + timedelta(days=x)
        v = day_to_value.get(d)
        if v is None:
            ys.append(np.nan)
        else:
            ys.append((float(v) / baseline_mean) * 100.0)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(xs, ys, linewidth=1.5, color="steelblue")
    ax.axvline(0, color="crimson", linestyle="--", linewidth=1.2, label="onset")
    ax.axhline(100, color="gray", linestyle=":", alpha=0.5)
    ax.set_xlabel("day offset from onset")
    ax.set_ylabel("index (baseline = 100)")
    ax.set_title(f"{label_id}   signal: {signal}")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(out_path, dpi=100)
    plt.close(fig)


def _render_placeholder(label_id, signal, out_path, message):
    """Render a full-size PNG carrying only `message` — no axes, no fake
    data. Used when an event cannot be normalized (e.g., zero baseline
    data). Every label_id gets a PNG so the labeller never opens a
    missing-file confusion; the message tells them to mark UNSURE."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.text(
        0.5, 0.5, message, ha="center", va="center", fontsize=18,
        transform=ax.transAxes,
    )
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(f"{label_id}   signal: {signal}")
    fig.tight_layout()
    fig.savefig(out_path, dpi=100)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Smoke test — synthetic series through the real rendering code path
# ---------------------------------------------------------------------------

def _smoke_test() -> int:
    """Render 3 synthetic plots to SMOKE_DIR. NO BigQuery. NO real series
    values. Purpose: verify normalization, axis labels, onset marker, and
    gap-as-gap rendering without opening any real sampled plot before
    labelling.

    Cases:
      A — clean step-down at onset (baseline flat 100 -> post flat 50);
          checks normalization + onset marker + index=50 on Y post-onset.
      B — noisy flat series (~100 pre and post, fixed-seed gaussian noise);
          checks that normal wobble stays within the ~[80, 120] band and
          that the plot doesn't scream "change" when there isn't one.
      C — two-level series (100 pre, 60 post) with a 10-day gap straddling
          the transition; verifies matplotlib does NOT draw a diagonal
          connector across the missing days — a gap must look like a gap,
          not like interpolation or a floor of zeros.
    """
    import random as _random

    SMOKE_DIR.mkdir(parents=True, exist_ok=True)
    onset_day = date(2026, 1, 15)  # arbitrary; smoke test doesn't touch BQ

    def _write(rows, label_id, signal, filename):
        baseline_mean, _ = _normalize(rows, onset_day)
        _render_plot(label_id, signal, rows, onset_day,
                     SMOKE_DIR / filename, baseline_mean)
        print(f"  wrote {filename}  (baseline_mean={baseline_mean:.2f})")

    # --- Case A: clean step-down
    rows_a: list[dict] = []
    for i in range(PLOT_PRE_DAYS, 0, -1):
        rows_a.append({"d": onset_day - timedelta(days=i), "value": 100.0})
    for i in range(PLOT_POST_DAYS + 1):
        rows_a.append({"d": onset_day + timedelta(days=i), "value": 50.0})
    _write(rows_a, "SMOKE_A", "aapv", "SMOKE_A_step_down.png")

    # --- Case B: noisy flat around baseline (fixed seed)
    rng = _random.Random(42)
    rows_b: list[dict] = []
    for i in range(PLOT_PRE_DAYS, 0, -1):
        rows_b.append({"d": onset_day - timedelta(days=i),
                       "value": 100.0 + rng.gauss(0, 8)})
    for i in range(PLOT_POST_DAYS + 1):
        rows_b.append({"d": onset_day + timedelta(days=i),
                       "value": 100.0 + rng.gauss(0, 8)})
    _write(rows_b, "SMOKE_B", "rpm", "SMOKE_B_noisy_flat.png")

    # --- Case C: step + 10-day gap straddling onset (days -4..+5)
    rows_c: list[dict] = []
    for i in range(PLOT_PRE_DAYS, 0, -1):
        val = None if 1 <= i <= 4 else 100.0
        rows_c.append({"d": onset_day - timedelta(days=i), "value": val})
    for i in range(PLOT_POST_DAYS + 1):
        val = None if 0 <= i <= 5 else 60.0
        rows_c.append({"d": onset_day + timedelta(days=i), "value": val})
    _write(rows_c, "SMOKE_C", "aapv", "SMOKE_C_gap.png")

    print(f"\nSmoke test complete — 3 plots in "
          f"{SMOKE_DIR.relative_to(REPO_ROOT)}. No BigQuery calls made.")
    return 0


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------

def _write_template(assigned, path: Path) -> None:
    """Blinded labeller file: no event_id, no entity, no verdict."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["label_id", "signal", "label", "reason",
                    "labeller", "labelled_at"])
        for r in assigned:
            w.writerow([r["label_id"], r["signal"], "", "", "", ""])


def _write_key(assigned, path: Path) -> None:
    """Rejoin key. Not to be read until labelling is complete."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(
            "# DO NOT OPEN until labelling is complete. This file joins "
            "label_id back to entity/verdict — it exists for "
            "reproducibility, not for reading.\n"
        )
        f.write(
            f"# sampling_date: {SAMPLING_DATE.isoformat()}, "
            f"random_seed: {RANDOM_SEED}, "
            f"config_version: {CONFIG_VERSION}\n"
        )
        w = csv.writer(f)
        w.writerow([
            "label_id", "event_id", "entity", "signal", "onset_ts",
            "check6_vote", "stratum", "short_post_window",
        ])
        for r in assigned:
            w.writerow([
                r["label_id"], r["event_id"], r["entity"], r["signal"],
                r["onset_ts"].isoformat(), r["check_6_status"],
                r["stratum"],
                "true" if r["short_post_window"] else "false",
            ])


def _write_graham_template(assigned, graham_label_ids, path: Path) -> None:
    label_to_signal = {r["label_id"]: r["signal"] for r in assigned}
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["label_id", "signal", "label", "reason",
                    "labeller", "labelled_at"])
        for lid in graham_label_ids:
            w.writerow([lid, label_to_signal[lid], "", "", "", ""])


# ---------------------------------------------------------------------------
# Validation (enforces the spec's mandatory-reason rule)
# ---------------------------------------------------------------------------

def validate_labels(path) -> list[str]:
    """Return list of human-readable problem strings; empty = valid.
    Enforces: label in {TRUE, FALSE, UNSURE}, non-empty reason, non-empty
    labeller. A blank reason is a HARD reject per benchmark/labels_v1_spec.md.
    """
    problems: list[str] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            lid = row.get("label_id", "?")
            label = (row.get("label") or "").strip()
            reason = (row.get("reason") or "").strip()
            labeller = (row.get("labeller") or "").strip()
            if not label:
                problems.append(f"row {i} ({lid}): empty label")
            elif label not in _VALID_LABELS:
                problems.append(
                    f"row {i} ({lid}): label {label!r} not in "
                    f"{sorted(_VALID_LABELS)}"
                )
            if not reason:
                problems.append(f"row {i} ({lid}): empty reason")
            if not labeller:
                problems.append(f"row {i} ({lid}): empty labeller")
    return problems


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _print_composition(assigned: list[dict]) -> None:
    """Counts only — never entity names. Blinding is on until labelling
    is complete."""
    print(f"\nSample composition ({len(assigned)} items total):")
    strata = {"survivor": 0, "killed": 0}
    signals: dict[str, int] = {}
    for r in assigned:
        strata[r["stratum"]] += 1
        signals[r["signal"]] = signals.get(r["signal"], 0) + 1
    print(f"  stratum: survivor={strata['survivor']}  "
          f"killed={strata['killed']}")
    for sig in sorted(signals):
        print(f"  signal:  {sig}={signals[sig]}")
    short = [r["label_id"] for r in assigned if r["short_post_window"]]
    print(
        f"  short_post_window (<{MIN_POST_DAYS_FOR_LABEL} post-days): "
        f"{len(short)}" + (f"  {short}" if short else "")
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the blinded v1 labelling package (44 events)."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true",
        help="Fetch + assign + print composition; skip files/plots.",
    )
    mode.add_argument(
        "--smoke-test", action="store_true",
        help="Render 3 synthetic plots to benchmark/plots_smoketest/ to "
             "verify the rendering pipeline. No BigQuery, no real series.",
    )
    args = parser.parse_args()

    if args.smoke_test:
        return _smoke_test()

    bq = _default_client()
    print(f"Fetching latest verdicts under {CONFIG_VERSION}...")
    latest = _fetch_latest_verdicts(bq)
    print(f"Got {len(latest)} events with a verdict under {CONFIG_VERSION}.")

    survivors, killed = _stratify(latest)
    print(f"Stratified: survivors={len(survivors)}, killed={len(killed)}")

    if (len(survivors) != EXPECTED_SURVIVORS
            or len(killed) != EXPECTED_KILLED):
        raise SystemExit(
            f"STOP: expected {EXPECTED_SURVIVORS} survivors and "
            f"{EXPECTED_KILLED} killed; got "
            f"{len(survivors)}/{len(killed)}."
        )

    rng = random.Random(RANDOM_SEED)
    assigned = _assign_sample(survivors, killed, rng)
    graham_ids = _pick_graham_subset(assigned, rng)

    meta = _fetch_events_meta(bq, [r["event_id"] for r in assigned])
    today = date.today()
    for r in assigned:
        m = meta[r["event_id"]]
        r["entity"] = m["entity"]
        r["signal"] = m["signal"]
        r["onset_ts"] = m["onset_ts"]
        post_days = _compute_post_days(m["onset_ts"], today)
        r["short_post_window"] = post_days < MIN_POST_DAYS_FOR_LABEL

    _print_composition(assigned)

    warned = [r["label_id"] for r in assigned if r["short_post_window"]]
    for lid in warned:
        print(
            f"  WARNING: {lid} has <{MIN_POST_DAYS_FOR_LABEL} post-onset "
            "days at sampling time — marked short_post_window in KEY."
        )

    if args.dry_run:
        print("\nDry run — no files written, no plots rendered.")
        return 0

    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nFetching series + rendering {len(assigned)} plots...")
    placeholders: list[str] = []
    unsupported: list[str] = []
    for i, r in enumerate(assigned, start=1):
        onset_day = r["onset_ts"].date()
        out_path = PLOTS_DIR / f"{r['label_id']}.png"
        signal_sql = _series_sql_for(r["signal"])
        if signal_sql is None:
            _render_placeholder(
                r["label_id"], r["signal"], out_path,
                "unsupported signal — cannot render",
            )
            unsupported.append(r["label_id"])
            print(f"  [{i:>2}/{len(assigned)}] {r['label_id']}  "
                  f"PLACEHOLDER (unsupported signal {r['signal']!r})")
            continue
        start = onset_day - timedelta(days=PLOT_PRE_DAYS)
        end = onset_day + timedelta(days=PLOT_POST_DAYS)
        rows = _fetch_series(bq, r["entity"], signal_sql, start, end)
        baseline_mean, baseline_n = _normalize(rows, onset_day)
        if baseline_mean is None or baseline_mean <= 0:
            _render_placeholder(
                r["label_id"], r["signal"], out_path,
                "insufficient baseline data to normalize",
            )
            placeholders.append(r["label_id"])
            print(f"  [{i:>2}/{len(assigned)}] {r['label_id']}  "
                  f"PLACEHOLDER (no baseline data, n={baseline_n})")
            continue
        _render_plot(r["label_id"], r["signal"], rows, onset_day,
                     out_path, baseline_mean)
        print(f"  [{i:>2}/{len(assigned)}] {r['label_id']}  "
              f"wrote {out_path.name}  (baseline n={baseline_n})")

    _write_template(assigned, TEMPLATE_PATH)
    print(f"\nWrote {TEMPLATE_PATH.relative_to(REPO_ROOT)}")
    _write_key(assigned, KEY_PATH)
    print(f"Wrote {KEY_PATH.relative_to(REPO_ROOT)}  "
          f"(do NOT open until labelling is complete)")
    _write_graham_template(assigned, graham_ids, GRAHAM_PATH)
    print(f"Wrote {GRAHAM_PATH.relative_to(REPO_ROOT)}")

    print(f"\nGraham's {len(graham_ids)} plot files to send him:")
    for lid in graham_ids:
        print(f"  benchmark/plots_v1/{lid}.png")

    if placeholders or unsupported:
        print(f"\nPlaceholders ({len(placeholders) + len(unsupported)}):")
        for lid in placeholders:
            print(f"  {lid}  (no baseline data)")
        for lid in unsupported:
            print(f"  {lid}  (unsupported signal)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
