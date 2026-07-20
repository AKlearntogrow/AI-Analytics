"""scripts/sample_practice_set.py — practice set for training labellers.

NOT part of the benchmark. Every practice event_id is guaranteed to be
absent from benchmark/labels_v1_KEY.csv — the intersection guard is
asserted at run time and STOPS with a loud error if any benchmark event
leaks into the practice pool.

Reuses scripts/sample_benchmark_v1._render_plot / _render_placeholder /
_normalize by IMPORT — not copy — so practising on differently-rendered
plots is structurally impossible. tests/test_sample_practice_set.py
pins this reuse with identity assertions.

Two output files:
  benchmark/practice_TEMPLATE.csv   — labeller opens this
  benchmark/practice_ANSWERS.csv    — DELIBERATELY meant to be opened
                                       AFTER labelling; feedback that
                                       makes practice useful. Contains no
                                       benchmark events, so revealing it
                                       costs nothing.

Usage:
  python scripts/sample_practice_set.py --dry-run   # composition only
  python scripts/sample_practice_set.py             # write files + plots
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import date, timedelta, timezone
from pathlib import Path

from detector.verifier import _default_client, _fetch_series, _series_sql_for

# Import the SAME rendering module used for the benchmark plots. scripts/
# is added to sys.path so this works whether the script is invoked
# directly or imported by tests.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import sample_benchmark_v1 as _sbv1


# ---- Constants ----
CONFIG_VERSION = "verifier-0.0.4-check3"
PRACTICE_SEED = 20260720
N_PRACTICE = 8
STRATUM_TARGET_SURVIVORS = 4
STRATUM_TARGET_KILLED = 4
# Plot geometry inherited from the benchmark sampler — practice charts
# must match the benchmark charts pixel-for-pixel wherever possible.
PLOT_PRE_DAYS = _sbv1.PLOT_PRE_DAYS
PLOT_POST_DAYS = _sbv1.PLOT_POST_DAYS

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_DIR = REPO_ROOT / "benchmark"
KEY_PATH = BENCHMARK_DIR / "labels_v1_KEY.csv"           # READ-ONLY here
PLOTS_DIR = BENCHMARK_DIR / "plots_practice"
BENCHMARK_PLOTS_DIR = BENCHMARK_DIR / "plots_v1"         # never written
TEMPLATE_PATH = BENCHMARK_DIR / "practice_TEMPLATE.csv"
ANSWERS_PATH = BENCHMARK_DIR / "practice_ANSWERS.csv"

# Import-time invariant: the two plot directories are distinct.
assert PLOTS_DIR != BENCHMARK_PLOTS_DIR
assert PLOTS_DIR.name != BENCHMARK_PLOTS_DIR.name


# ---------------------------------------------------------------------------
# KEY reader — ONLY the event_id column is extracted; nothing else is
# read or printed. The KEY file remains blinded.
# ---------------------------------------------------------------------------

def _read_benchmark_event_ids(path: Path) -> set[str]:
    if not path.exists():
        raise SystemExit(
            f"STOP: {path} not found. The practice sampler MUST be able "
            "to read the benchmark KEY to build its exclusion list. Run "
            "scripts/sample_benchmark_v1.py first."
        )
    with open(path, newline="", encoding="utf-8") as f:
        lines = f.readlines()
    # Skip any leading '#' comment lines (header notes).
    data_lines = [ln for ln in lines if not ln.startswith("#")]
    reader = csv.DictReader(data_lines)
    ids: set[str] = set()
    for row in reader:
        eid = row.get("event_id")
        if eid:
            ids.add(eid)
    return ids


# ---------------------------------------------------------------------------
# BigQuery fetch
# ---------------------------------------------------------------------------

def _fetch_latest_verdicts(bq) -> list[dict]:
    """Latest verdict per event under CONFIG_VERSION. Extracts check_6
    status plus check_3 classification + est_daily_revenue_delta for the
    ANSWERS file."""
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
        c3 = cr.get("check_3", {})
        out.append({
            "event_id": r["event_id"],
            "check_6_status": cr["check_6"]["status"],
            "check_3_class": c3.get("classification", "ERRORED"),
            "check_3_delta": c3.get("est_daily_revenue_delta"),
        })
    return out


def _fetch_events_meta(bq, event_ids) -> dict:
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
# Pure selection (deterministic; tested)
# ---------------------------------------------------------------------------

def _balanced_pick(items: list[dict], k: int, rng: random.Random) -> list[dict]:
    """Try to split k roughly evenly between aapv and rpm. If a signal is
    short, top up from the other. Returns exactly min(k, len(items))."""
    aapv = [x for x in items if x["signal"] == "aapv"]
    rpm = [x for x in items if x["signal"] == "rpm"]
    rng.shuffle(aapv)
    rng.shuffle(rpm)
    n_each = k // 2
    pick_a = aapv[:min(n_each, len(aapv))]
    pick_r = rpm[:min(k - len(pick_a), len(rpm))]
    if len(pick_a) + len(pick_r) < k:
        remaining_a = aapv[len(pick_a):]
        pick_a.extend(remaining_a[:k - len(pick_a) - len(pick_r)])
    return pick_a + pick_r


def _select_practice(candidates: list[dict], rng: random.Random) -> list[dict]:
    """Stratified pick: aim for STRATUM_TARGET_SURVIVORS + STRATUM_TARGET_KILLED
    with a signal mix. If a stratum can't be filled, top up from the other
    and report actual composition via _print_composition.

    Assigns P001..P00N label IDs after shuffle. Deterministic in rng state.
    """
    survivors = sorted(
        [c for c in candidates
         if c["check_6_status"] in ("SUPPORT", "NEUTRAL")],
        key=lambda r: r["event_id"],
    )
    killed = sorted(
        [c for c in candidates if c["check_6_status"] == "UNDERMINE"],
        key=lambda r: r["event_id"],
    )

    n_s_target = min(STRATUM_TARGET_SURVIVORS, len(survivors))
    n_k_target = min(STRATUM_TARGET_KILLED, len(killed))
    short = N_PRACTICE - (n_s_target + n_k_target)
    if short > 0:
        # Top up from whichever stratum has excess.
        if len(survivors) - n_s_target >= short:
            n_s_target += short
        else:
            n_k_target = min(n_k_target + short, len(killed))

    picked_s = _balanced_pick(survivors, n_s_target, rng)
    picked_k = _balanced_pick(killed, n_k_target, rng)
    combined = picked_s + picked_k
    rng.shuffle(combined)
    for i, item in enumerate(combined, start=1):
        item["practice_id"] = f"P{i:03d}"
    return combined


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------

def _write_template(assigned, path: Path) -> None:
    """Same shape as the benchmark template so practising matches the real
    task."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["practice_id", "signal", "label", "reason"])
        for r in assigned:
            w.writerow([r["practice_id"], r["signal"], "", ""])


def _write_answers(assigned, path: Path) -> None:
    """DELIBERATE difference from the benchmark KEY: this file IS meant to
    be opened, AFTER labelling a practice chart, so the labeller can see
    what the verifier's current view of the same event was. Contains only
    practice events; no benchmark event appears here."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(
            "# ANSWER KEY for the practice set. OPEN AFTER labelling each "
            "practice chart — this is the feedback that makes practice "
            "useful. Contains ONLY practice events; no benchmark event "
            "appears here.\n"
        )
        w = csv.writer(f)
        w.writerow([
            "practice_id", "event_id", "entity", "signal", "onset_ts",
            "check6_vote", "check3_classification", "est_daily_revenue_delta",
        ])
        for r in assigned:
            delta = r["check_3_delta"]
            w.writerow([
                r["practice_id"], r["event_id"], r["entity"], r["signal"],
                r["onset_ts"].isoformat(),
                r["check_6_status"], r["check_3_class"],
                f"{delta:.2f}" if delta is not None else "",
            ])


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _print_composition(assigned: list[dict]) -> None:
    """Counts only — never entity names."""
    print(f"\nPractice composition ({len(assigned)} items):")
    strata = {"survivor": 0, "killed": 0}
    signals: dict[str, int] = {}
    for r in assigned:
        s = ("survivor"
             if r["check_6_status"] in ("SUPPORT", "NEUTRAL")
             else "killed")
        strata[s] += 1
        signals[r["signal"]] = signals.get(r["signal"], 0) + 1
    print(f"  stratum: survivor={strata['survivor']}  "
          f"killed={strata['killed']}")
    for sig in sorted(signals):
        print(f"  signal:  {sig}={signals[sig]}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sample 8 practice events (excluded from benchmark)."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch + select + report composition; skip files/plots.",
    )
    args = parser.parse_args()

    bq = _default_client()

    print(f"Reading benchmark exclusion list from "
          f"{KEY_PATH.relative_to(REPO_ROOT)}...")
    benchmark_ids = _read_benchmark_event_ids(KEY_PATH)
    print(f"  {len(benchmark_ids)} benchmark event_ids to exclude.")

    print(f"Fetching latest verdicts under {CONFIG_VERSION}...")
    latest = _fetch_latest_verdicts(bq)
    print(f"  {len(latest)} events with a verdict.")

    candidates = [r for r in latest if r["event_id"] not in benchmark_ids]
    leaked = benchmark_ids & {r["event_id"] for r in candidates}
    if leaked:
        raise SystemExit(
            f"STOP: {len(leaked)} benchmark event_ids leaked into the "
            "practice candidate pool. Exclusion filter is broken."
        )
    print(f"  {len(candidates)} candidate events after exclusion.")

    meta = _fetch_events_meta(bq, [r["event_id"] for r in candidates])
    for r in candidates:
        m = meta[r["event_id"]]
        r["entity"] = m["entity"]
        r["signal"] = m["signal"]
        r["onset_ts"] = m["onset_ts"]

    rng = random.Random(PRACTICE_SEED)
    assigned = _select_practice(candidates, rng)

    # Belt-and-braces post-selection check.
    picked_ids = {r["event_id"] for r in assigned}
    overlap = picked_ids & benchmark_ids
    if overlap:
        raise SystemExit(
            f"STOP: assigned practice contains benchmark event_ids: "
            f"{len(overlap)} overlap."
        )

    _print_composition(assigned)

    if args.dry_run:
        print("\nDry run — no files written, no plots rendered.")
        return 0

    PLOTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nRendering {len(assigned)} practice plots using the SAME "
          "code path as the benchmark plots...")
    for i, r in enumerate(assigned, start=1):
        onset_day = r["onset_ts"].date()
        out_path = PLOTS_DIR / f"{r['practice_id']}.png"
        # Sanity: never write inside plots_v1/.
        assert BENCHMARK_PLOTS_DIR.resolve() not in out_path.resolve().parents, (
            f"practice plot path leaked into plots_v1/: {out_path}"
        )
        signal_sql = _series_sql_for(r["signal"])
        if signal_sql is None:
            _sbv1._render_placeholder(
                r["practice_id"], r["signal"], out_path,
                "unsupported signal — cannot render",
            )
            print(f"  [{i}/{len(assigned)}] {r['practice_id']}  PLACEHOLDER")
            continue
        start = onset_day - timedelta(days=PLOT_PRE_DAYS)
        end = onset_day + timedelta(days=PLOT_POST_DAYS)
        rows = _fetch_series(bq, r["entity"], signal_sql, start, end)
        baseline_mean, baseline_n = _sbv1._normalize(rows, onset_day)
        if baseline_mean is None or baseline_mean <= 0:
            _sbv1._render_placeholder(
                r["practice_id"], r["signal"], out_path,
                "insufficient baseline data to normalize",
            )
            print(f"  [{i}/{len(assigned)}] {r['practice_id']}  "
                  f"PLACEHOLDER (no baseline data, n={baseline_n})")
            continue
        _sbv1._render_plot(
            r["practice_id"], r["signal"], rows, onset_day,
            out_path, baseline_mean,
        )
        print(f"  [{i}/{len(assigned)}] {r['practice_id']}  wrote "
              f"{out_path.name}  (baseline n={baseline_n})")

    _write_template(assigned, TEMPLATE_PATH)
    print(f"\nWrote {TEMPLATE_PATH.relative_to(REPO_ROOT)}")
    _write_answers(assigned, ANSWERS_PATH)
    print(f"Wrote {ANSWERS_PATH.relative_to(REPO_ROOT)}  "
          "(open AFTER labelling — this is the feedback)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
