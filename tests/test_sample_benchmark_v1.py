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


def test_pick_se_subset_swaps_when_must_include_missing():
    """L001-not-in-15 → swap in L001, drop alphabetically-last survivor."""
    current_15 = [
        "L002", "L008", "L009", "L011", "L016", "L021", "L033",
        "L034", "L036", "L040",              # 10 survivors
        "L004", "L018", "L019", "L029", "L031",  # 5 killed
    ]
    label_to_stratum = {lid: "survivor" for lid in
                        ("L001", "L002", "L008", "L009", "L011", "L016",
                         "L021", "L033", "L034", "L036", "L040")}
    label_to_stratum.update({lid: "killed" for lid in
                             ("L004", "L018", "L019", "L029", "L031")})
    new_15, note = sbv1._pick_se_subset(current_15, label_to_stratum, "L001")
    assert len(new_15) == 15
    assert "L001" in new_15
    assert "L040" not in new_15, "should drop L040 as alphabetically-last survivor"
    surv = [lid for lid in new_15 if label_to_stratum[lid] == "survivor"]
    kill = [lid for lid in new_15 if label_to_stratum[lid] == "killed"]
    assert len(surv) == 10 and len(kill) == 5
    assert "L040" in note and "L001" in note


def test_pick_se_subset_noop_when_must_include_already_present():
    current_15 = ["L001", "L002", "L004"]
    label_to_stratum = {"L001": "survivor", "L002": "survivor", "L004": "killed"}
    new_15, note = sbv1._pick_se_subset(current_15, label_to_stratum, "L001")
    assert new_15 == sorted(current_15)
    assert "no swap needed" in note


def test_pick_se_subset_stops_when_no_same_stratum_to_drop():
    import pytest
    current_15 = ["L004", "L018"]  # all killed
    label_to_stratum = {"L001": "survivor", "L004": "killed", "L018": "killed"}
    with pytest.raises(SystemExit, match="cannot preserve"):
        sbv1._pick_se_subset(current_15, label_to_stratum, "L001")


def _fake_44_label_to_stratum():
    """44 label_ids matching the real sample: 29 survivors + 15 killed."""
    surv = {f"L{i:03d}": "survivor" for i in range(1, 30)}
    kill = {f"L{i:03d}": "killed" for i in range(30, 45)}
    return {**surv, **kill}


def test_pick_core_and_blocks_is_deterministic():
    """Same seed + same inputs → identical assignment."""
    lts = _fake_44_label_to_stratum()
    a = sbv1._pick_core_and_blocks(lts, "L001", sbv1.RANDOM_SEED)
    b = sbv1._pick_core_and_blocks(lts, "L001", sbv1.RANDOM_SEED)
    assert a == b


def test_pick_core_and_blocks_shape_and_composition():
    lts = _fake_44_label_to_stratum()
    sets = sbv1._pick_core_and_blocks(lts, "L001", sbv1.RANDOM_SEED)
    assert len(sets["core"]) == sbv1._CORE_N == 14
    for r in sbv1._RATERS:
        assert len(sets[r]) == sbv1._BLOCK_N == 10
    core_surv = [lid for lid in sets["core"] if lts[lid] == "survivor"]
    core_kill = [lid for lid in sets["core"] if lts[lid] == "killed"]
    assert len(core_surv) == sbv1._CORE_N_SURVIVORS == 9
    assert len(core_kill) == sbv1._CORE_N_KILLED == 5


def test_pick_core_and_blocks_must_include_lands_in_core():
    lts = _fake_44_label_to_stratum()
    sets = sbv1._pick_core_and_blocks(lts, "L001", sbv1.RANDOM_SEED)
    assert "L001" in sets["core"]
    for r in sbv1._RATERS:
        assert "L001" not in sets[r]


def test_pick_core_and_blocks_blocks_are_disjoint():
    lts = _fake_44_label_to_stratum()
    sets = sbv1._pick_core_and_blocks(lts, "L001", sbv1.RANDOM_SEED)
    r1, r2, r3 = set(sets["r1"]), set(sets["r2"]), set(sets["r3"])
    assert not (r1 & r2)
    assert not (r1 & r3)
    assert not (r2 & r3)


def test_pick_core_and_blocks_covers_all_44():
    lts = _fake_44_label_to_stratum()
    sets = sbv1._pick_core_and_blocks(lts, "L001", sbv1.RANDOM_SEED)
    covered = set(sets["core"]) | set(sets["r1"]) | set(sets["r2"]) | set(sets["r3"])
    assert covered == set(lts.keys())
    assert len(covered) == 44


def test_pick_core_and_blocks_blocks_disjoint_from_core():
    lts = _fake_44_label_to_stratum()
    sets = sbv1._pick_core_and_blocks(lts, "L001", sbv1.RANDOM_SEED)
    core = set(sets["core"])
    for r in sbv1._RATERS:
        assert not (core & set(sets[r]))


def test_pick_core_and_blocks_stops_when_must_include_not_survivor():
    lts = _fake_44_label_to_stratum()
    with pytest.raises(SystemExit, match="must be a survivor"):
        sbv1._pick_core_and_blocks(lts, "L030", sbv1.RANDOM_SEED)  # L030 is killed


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
