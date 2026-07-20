"""Determinism + shape tests for scripts/sample_benchmark_v1.

The sampling script's pure logic (_assign_sample, _pick_graham_subset)
is exercised here with synthetic inputs so tests never touch BigQuery.
Plot rendering and CSV writing are NOT tested — they're one-time
throwaway artifacts that must be eyeballed. What matters here is:

  1. Same seed -> same label_id -> event_id mapping across runs.
  2. 44 items assigned, stratum counts as spec'd (29 / 15).
  3. Label IDs L001..L044, contiguous.
  4. Graham's subset is 10 survivors + 5 killed, deterministic.
  5. Input count guards fire.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

# scripts/ isn't a package; add it to sys.path so we can import the module.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import sample_benchmark_v1 as sbv1


def _fake_survivors():
    return [{"event_id": f"srv-{i:03d}", "check_6_status": "SUPPORT"}
            for i in range(sbv1.EXPECTED_SURVIVORS)]


def _fake_killed():
    return [{"event_id": f"kil-{i:03d}", "check_6_status": "UNDERMINE"}
            for i in range(sbv1.EXPECTED_KILLED)]


def _run_pipeline():
    """Exercise _assign_sample followed by _pick_graham_subset with the
    real RANDOM_SEED — same call order the CLI uses."""
    rng = random.Random(sbv1.RANDOM_SEED)
    assigned = sbv1._assign_sample(_fake_survivors(), _fake_killed(), rng)
    graham = sbv1._pick_graham_subset(assigned, rng)
    return assigned, graham


def test_assign_sample_is_deterministic_across_runs():
    a1, _ = _run_pipeline()
    a2, _ = _run_pipeline()
    m1 = {r["label_id"]: r["event_id"] for r in a1}
    m2 = {r["label_id"]: r["event_id"] for r in a2}
    assert m1 == m2, "label_id -> event_id must be stable across runs"


def test_assign_sample_produces_44_with_correct_strata():
    a, _ = _run_pipeline()
    assert len(a) == sbv1.N_TOTAL == 44
    strata = {"survivor": 0, "killed": 0}
    for r in a:
        strata[r["stratum"]] += 1
    assert strata == {"survivor": 29, "killed": 15}


def test_label_ids_are_L001_to_L044_contiguous():
    a, _ = _run_pipeline()
    ids = sorted(r["label_id"] for r in a)
    assert ids == [f"L{i:03d}" for i in range(1, sbv1.N_TOTAL + 1)]


def test_shuffle_interleaves_survivors_and_killed():
    """Blinding guarantee: strata are not clumped by label_id — a labeller
    can't infer stratum from position."""
    a, _ = _run_pipeline()
    ordered = sorted(a, key=lambda r: r["label_id"])
    # Both strata should appear in the FIRST HALF (label_ids L001..L022).
    first_half_strata = {r["stratum"] for r in ordered[:22]}
    assert first_half_strata == {"survivor", "killed"}


def test_graham_subset_is_deterministic_and_stratified():
    _, g1 = _run_pipeline()
    _, g2 = _run_pipeline()
    assert g1 == g2, "Graham's label_id picks must be stable across runs"
    assert len(g1) == sbv1.GRAHAM_N_SURVIVORS + sbv1.GRAHAM_N_KILLED == 15
    a, _ = _run_pipeline()
    lid_to_stratum = {r["label_id"]: r["stratum"] for r in a}
    graham_strata = [lid_to_stratum[lid] for lid in g1]
    assert graham_strata.count("survivor") == sbv1.GRAHAM_N_SURVIVORS
    assert graham_strata.count("killed") == sbv1.GRAHAM_N_KILLED


def test_graham_label_ids_are_a_subset_of_the_44():
    a, g = _run_pipeline()
    all_lids = {r["label_id"] for r in a}
    assert set(g).issubset(all_lids)


def test_survivor_count_guard_fires():
    rng = random.Random(sbv1.RANDOM_SEED)
    with pytest.raises(ValueError, match="expected 29 survivors"):
        sbv1._assign_sample(_fake_survivors()[:20], _fake_killed(), rng)


def test_killed_count_guard_fires():
    rng = random.Random(sbv1.RANDOM_SEED)
    with pytest.raises(ValueError, match="expected 47 killed"):
        sbv1._assign_sample(_fake_survivors(), _fake_killed()[:30], rng)


def test_stratify_rejects_unexpected_check_6_status():
    verdicts = _fake_survivors() + [{"event_id": "weird", "check_6_status": "MAYBE"}]
    with pytest.raises(SystemExit, match="unexpected check_6 statuses"):
        sbv1._stratify(verdicts)


def test_recency_guard_flags_short_window():
    """MIN_POST_DAYS_FOR_LABEL = 30 boundary check on _compute_post_days."""
    from datetime import date, datetime, timezone
    today = date(2026, 7, 20)
    # 29 days after onset -> triggers short_post_window
    onset = datetime(2026, 6, 21, tzinfo=timezone.utc)
    assert sbv1._compute_post_days(onset, today) == 29
    assert 29 < sbv1.MIN_POST_DAYS_FOR_LABEL
    # 30 days -> exactly at the threshold, NOT short
    onset = datetime(2026, 6, 20, tzinfo=timezone.utc)
    assert sbv1._compute_post_days(onset, today) == 30
    # 90 days -> capped at PLOT_POST_DAYS = 60
    onset = datetime(2026, 4, 21, tzinfo=timezone.utc)
    assert sbv1._compute_post_days(onset, today) == sbv1.PLOT_POST_DAYS


def test_normalize_returns_none_when_no_baseline_data():
    from datetime import date
    onset_day = date(2026, 5, 23)
    # All rows post-onset -> no baseline
    rows = [{"d": date(2026, 5, 24), "value": 100.0}]
    mean, n = sbv1._normalize(rows, onset_day)
    assert mean is None
    assert n == 0


def test_normalize_drops_nulls():
    from datetime import date, timedelta
    onset_day = date(2026, 5, 23)
    rows = []
    for i in range(1, sbv1.BASELINE_DAYS + 1):
        # alternate value and None
        val = 100.0 if i % 2 == 0 else None
        rows.append({"d": onset_day - timedelta(days=i), "value": val})
    mean, n = sbv1._normalize(rows, onset_day)
    assert mean == 100.0
    assert n == sbv1.BASELINE_DAYS // 2


def test_validate_labels_flags_all_error_shapes(tmp_path):
    import csv
    p = tmp_path / "labels.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["label_id", "signal", "label", "reason", "labeller", "labelled_at"])
        w.writerow(["L001", "aapv", "TRUE", "clear drop", "akhil", "2026-07-20"])   # OK
        w.writerow(["L002", "rpm", "", "no idea", "akhil", "2026-07-20"])           # empty label
        w.writerow(["L003", "aapv", "MAYBE", "hmm", "akhil", "2026-07-20"])         # bad label
        w.writerow(["L004", "rpm", "FALSE", "", "akhil", "2026-07-20"])             # empty reason
        w.writerow(["L005", "aapv", "TRUE", "clear", "", "2026-07-20"])             # empty labeller
    problems = sbv1.validate_labels(p)
    assert any("L002" in p and "empty label" in p for p in problems)
    assert any("L003" in p and "MAYBE" in p for p in problems)
    assert any("L004" in p and "empty reason" in p for p in problems)
    assert any("L005" in p and "empty labeller" in p for p in problems)
    # L001 valid -> no problems mentioning it
    assert not any("L001" in p for p in problems)
