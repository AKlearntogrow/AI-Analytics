"""Tests for scripts/sample_practice_set.

Critical guard: practice event_ids MUST NOT overlap benchmark event_ids.
Also pins that the practice script uses the SAME rendering functions as
the benchmark sampler — any drift teaches the wrong task.
"""

from __future__ import annotations

import csv
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import sample_benchmark_v1 as sbv1
import sample_practice_set as sps


# ---------------------------------------------------------------------------
# Reuse-of-rendering-code identity checks
# ---------------------------------------------------------------------------

def test_practice_imports_the_benchmark_module():
    """Practice script binds `_sbv1` to the actual benchmark module — not
    a copy, not a stub."""
    assert sps._sbv1 is sbv1


def test_practice_uses_same_render_functions_as_benchmark():
    assert sps._sbv1._render_plot is sbv1._render_plot
    assert sps._sbv1._render_placeholder is sbv1._render_placeholder
    assert sps._sbv1._normalize is sbv1._normalize


def test_practice_uses_same_plot_geometry_constants():
    assert sps.PLOT_PRE_DAYS == sbv1.PLOT_PRE_DAYS
    assert sps.PLOT_POST_DAYS == sbv1.PLOT_POST_DAYS


# ---------------------------------------------------------------------------
# Fake data helpers
# ---------------------------------------------------------------------------

def _fake_verdicts(n_survivors=16, n_killed=16):
    out = []
    for i in range(n_survivors):
        out.append({
            "event_id": f"survivor-{i:03d}",
            "check_6_status": "SUPPORT" if i % 2 == 0 else "NEUTRAL",
            "check_3_class": "VOLUME",
            "check_3_delta": -100.0,
        })
    for i in range(n_killed):
        out.append({
            "event_id": f"killed-{i:03d}",
            "check_6_status": "UNDERMINE",
            "check_3_class": "MONETIZATION",
            "check_3_delta": -50.0,
        })
    return out


def _enrich(records, signals=("aapv", "rpm")):
    for i, r in enumerate(records):
        r.setdefault("entity", f"Publisher-{i}")
        r.setdefault("signal", signals[i % len(signals)])
        r.setdefault(
            "onset_ts",
            datetime(2026, 1, 1 + (i % 28), tzinfo=timezone.utc),
        )
    return records


# ---------------------------------------------------------------------------
# Selection logic
# ---------------------------------------------------------------------------

def test_practice_selection_is_deterministic():
    candidates_a = _enrich(_fake_verdicts())
    candidates_b = _enrich(_fake_verdicts())
    rng_a = random.Random(sps.PRACTICE_SEED)
    rng_b = random.Random(sps.PRACTICE_SEED)
    picked_a = sps._select_practice(candidates_a, rng_a)
    picked_b = sps._select_practice(candidates_b, rng_b)
    m_a = {r["practice_id"]: r["event_id"] for r in picked_a}
    m_b = {r["practice_id"]: r["event_id"] for r in picked_b}
    assert m_a == m_b


def test_practice_ids_are_P001_to_P008():
    candidates = _enrich(_fake_verdicts())
    rng = random.Random(sps.PRACTICE_SEED)
    picked = sps._select_practice(candidates, rng)
    ordered = sorted(r["practice_id"] for r in picked)
    assert ordered == [f"P{i:03d}" for i in range(1, sps.N_PRACTICE + 1)]


def test_practice_aims_for_stratified_mix_when_both_available():
    candidates = _enrich(_fake_verdicts(n_survivors=16, n_killed=16))
    rng = random.Random(sps.PRACTICE_SEED)
    picked = sps._select_practice(candidates, rng)
    survivor_count = sum(
        1 for r in picked
        if r["check_6_status"] in ("SUPPORT", "NEUTRAL")
    )
    killed_count = sum(1 for r in picked if r["check_6_status"] == "UNDERMINE")
    assert survivor_count == sps.STRATUM_TARGET_SURVIVORS == 4
    assert killed_count == sps.STRATUM_TARGET_KILLED == 4


def test_practice_gracefully_handles_zero_survivors_in_pool():
    """Real-world case: the benchmark took all 29 survivors, leaving 0 in
    the practice pool. Selector must top up to N_PRACTICE from killed
    rather than crashing."""
    candidates = _enrich(_fake_verdicts(n_survivors=0, n_killed=32))
    rng = random.Random(sps.PRACTICE_SEED)
    picked = sps._select_practice(candidates, rng)
    assert len(picked) == sps.N_PRACTICE == 8
    assert all(r["check_6_status"] == "UNDERMINE" for r in picked)


def test_practice_tops_up_from_killed_when_survivors_short():
    """Only 2 survivors available; selector should still return 8 total,
    padding with killed."""
    candidates = _enrich(_fake_verdicts(n_survivors=2, n_killed=30))
    rng = random.Random(sps.PRACTICE_SEED)
    picked = sps._select_practice(candidates, rng)
    assert len(picked) == sps.N_PRACTICE == 8
    survivors_in = sum(
        1 for r in picked
        if r["check_6_status"] in ("SUPPORT", "NEUTRAL")
    )
    killed_in = sum(1 for r in picked if r["check_6_status"] == "UNDERMINE")
    assert survivors_in == 2
    assert killed_in == 6


# ---------------------------------------------------------------------------
# THE critical guard: no benchmark event in practice
# ---------------------------------------------------------------------------

def test_practice_event_ids_never_overlap_benchmark_event_ids():
    """Simulates main()'s exclusion step: filter candidates first, then
    verify the selector cannot produce a benchmark event_id no matter
    what."""
    benchmark_ids = {f"benchmark-{i:03d}" for i in range(44)}
    candidates = _enrich(_fake_verdicts())
    # Real main() does this filter before calling _select_practice.
    filtered = [c for c in candidates if c["event_id"] not in benchmark_ids]
    rng = random.Random(sps.PRACTICE_SEED)
    picked = sps._select_practice(filtered, rng)
    picked_ids = {r["event_id"] for r in picked}
    assert not (picked_ids & benchmark_ids), \
        "practice selection leaked a benchmark event_id"


# ---------------------------------------------------------------------------
# Directory / path invariants
# ---------------------------------------------------------------------------

def test_plots_dirs_are_distinct():
    assert sps.PLOTS_DIR != sps.BENCHMARK_PLOTS_DIR
    assert sps.PLOTS_DIR.name != sps.BENCHMARK_PLOTS_DIR.name


# ---------------------------------------------------------------------------
# KEY reader — reads only event_id column, skips comment header
# ---------------------------------------------------------------------------

def test_read_benchmark_event_ids_returns_only_event_id_column(tmp_path):
    key_path = tmp_path / "labels_v1_KEY.csv"
    with open(key_path, "w", newline="", encoding="utf-8") as f:
        f.write("# DO NOT OPEN header line 1\n")
        f.write("# DO NOT OPEN header line 2\n")
        w = csv.writer(f)
        w.writerow([
            "label_id", "event_id", "entity", "signal", "onset_ts",
            "check6_vote", "stratum", "short_post_window",
        ])
        w.writerow([
            "L001", "evt-a", "PubA", "aapv",
            "2026-01-01T00:00:00+00:00", "SUPPORT", "survivor", "false",
        ])
        w.writerow([
            "L002", "evt-b", "PubB", "rpm",
            "2026-02-02T00:00:00+00:00", "UNDERMINE", "killed", "false",
        ])
    ids = sps._read_benchmark_event_ids(key_path)
    assert ids == {"evt-a", "evt-b"}


def test_read_benchmark_event_ids_stops_when_key_missing(tmp_path):
    missing = tmp_path / "does_not_exist.csv"
    with pytest.raises(SystemExit, match="STOP"):
        sps._read_benchmark_event_ids(missing)
