"""Tests for detector.verifier.verify_event — checks 1 & 2, BigQuery mocked.

Coverage:
  Check 1 (data-integrity gate, terminal on FAIL) — tracer cases carried over.
  Check 2 (persistence) — SUPPORT, UNDERMINE, three NEUTRAL paths, boundary,
    RPM sums-then-divide aggregation.
  Verdict wiring across the two checks; hard read-only constraint.

All BigQuery I/O is stubbed via a fake client — no ADC, no network.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from detector import verify_event
from detector import verifier as verifier_mod


_DEFAULT_ONSET_DATE = date(2026, 5, 23)


# ---------------------------------------------------------------------------
# fake BigQuery client
# ---------------------------------------------------------------------------

class _FakeQueryJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return iter(self._rows)


class _FakeBQ:
    """Routes SQL by substring:
      - "COUNT(*)"                       -> check 1 (count row)
      - "rev_tracker" (non-COUNT)        -> check 2 (daily series)
      - "operational_intelligence.events" -> event fetch
    """

    def __init__(
        self,
        *,
        event_rows,
        rev_tracker_count=0,
        check_2_series=None,
        check_2_raises=False,
        insert_errors=None,
    ):
        self._event_rows = event_rows
        self._rev_tracker_count = rev_tracker_count
        self._check_2_series = check_2_series if check_2_series is not None else []
        self._check_2_raises = check_2_raises
        self._insert_errors = insert_errors or []
        self.queries = []
        self.inserts = []

    def query(self, sql, job_config=None):
        self.queries.append((sql, job_config))
        if "COUNT(*)" in sql:
            return _FakeQueryJob([{"n": self._rev_tracker_count}])
        if "rev_tracker" in sql:
            if self._check_2_raises:
                raise RuntimeError("simulated check-2 BQ error")
            return _FakeQueryJob(self._check_2_series)
        if "operational_intelligence.events" in sql:
            return _FakeQueryJob(self._event_rows)
        raise AssertionError(f"unexpected SQL in test: {sql}")

    def insert_rows_json(self, table, rows):
        self.inserts.append((table, rows))
        return self._insert_errors


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

def _event_row(
    *,
    entity="Publift",
    signal="aapv",
    onset_ts=datetime(2026, 5, 23, tzinfo=timezone.utc),
):
    return {"entity": entity, "signal": signal, "onset_ts": onset_ts}


def _series(
    baseline_val,
    post_val,
    *,
    baseline_days=28,
    post_days=15,
    onset=_DEFAULT_ONSET_DATE,
):
    """Build a synthetic rev_tracker daily series.

    `baseline_days` days strictly before onset with value `baseline_val`;
    `post_days` days from onset onward with value `post_val`. Values may be
    numeric or None (None simulates a missing row).
    """
    rows = []
    for i in range(baseline_days, 0, -1):
        rows.append({"d": onset - timedelta(days=i), "value": baseline_val})
    for i in range(post_days):
        rows.append({"d": onset + timedelta(days=i), "value": post_val})
    return rows


# ---------------------------------------------------------------------------
# check-1 tracer cases (updated for the new verdict wiring)
# ---------------------------------------------------------------------------

def test_check_1_pass_and_check_2_support_yields_pass_with_caveats():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=17,
        check_2_series=_series(100.0, 50.0),  # post half of baseline → SUPPORT
    )

    result = verify_event("evt-123", client=bq)

    assert result["verdict"] == "PASS_WITH_CAVEATS"
    assert result["check_results"]["check_1"]["status"] == "PASS"
    assert result["check_results"]["check_1"]["rows_found"] == 17
    assert result["check_results"]["check_2"]["status"] == "SUPPORT"
    for name in ("check_3", "check_4", "check_5", "check_6", "check_7"):
        assert result["check_results"][name] == {"status": "NOT_IMPLEMENTED"}
    # check_2 was SUPPORT, so only the standing "checks 3–7" caveat remains.
    assert result["check_results"]["caveats"] == ["checks 3–7 not yet implemented"]
    assert result["config_version"] == "verifier-0.0.2-check2"

    assert len(bq.inserts) == 1
    table, rows = bq.inserts[0]
    assert table == "marble-light.operational_intelligence.verdicts"
    row = rows[0]
    assert row["event_id"] == "evt-123"
    assert row["verdict"] == "PASS_WITH_CAVEATS"
    assert row["config_version"] == "verifier-0.0.2-check2"
    assert row["brief_md"] is None
    assert row["onset_ts"] == "2026-05-23T00:00:00+00:00"

    parsed_checks = json.loads(row["check_results"])
    assert parsed_checks["check_1"]["status"] == "PASS"
    assert parsed_checks["check_2"]["status"] == "SUPPORT"
    assert parsed_checks["caveats"] == ["checks 3–7 not yet implemented"]

    parsed_refs = json.loads(row["evidence_refs"])
    assert "check_1_sql" in parsed_refs
    assert "check_2_sql" in parsed_refs
    assert parsed_refs["check_1_sql"] == result["evidence_refs"]["check_1_sql"]
    assert parsed_refs["check_2_sql"] == result["evidence_refs"]["check_2_sql"]


def test_check_1_fail_yields_kill():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=0,
        check_2_series=_series(100.0, 50.0),  # even a SUPPORTing series can't save a check_1 FAIL
    )

    result = verify_event("evt-456", client=bq)

    assert result["verdict"] == "KILL"
    assert result["check_results"]["check_1"]["status"] == "FAIL"
    assert result["check_results"]["check_1"]["rows_found"] == 0
    assert "caveats" not in result["check_results"]

    assert len(bq.inserts) == 1
    row = bq.inserts[0][1][0]
    assert row["verdict"] == "KILL"
    parsed = json.loads(row["check_results"])
    assert parsed["check_1"]["status"] == "FAIL"


def test_event_not_found_raises_and_writes_nothing():
    bq = _FakeBQ(event_rows=[])

    with pytest.raises(LookupError, match="event_id not found"):
        verify_event("evt-missing", client=bq)

    # No rev_tracker SELECT and no verdict row.
    assert bq.inserts == []
    assert all("rev_tracker" not in sql for sql, _ in bq.queries)


# ---------------------------------------------------------------------------
# structural / hard-constraint guarantees
# ---------------------------------------------------------------------------

def test_events_then_check1_then_check2_then_verdicts_insert():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=5,
        check_2_series=_series(100.0, 50.0),
    )
    verify_event("evt-order", client=bq)

    assert len(bq.queries) == 3
    assert "operational_intelligence.events" in bq.queries[0][0]
    assert "COUNT(*)" in bq.queries[1][0]
    check_2_sql = bq.queries[2][0]
    assert "rev_tracker" in check_2_sql and "COUNT(*)" not in check_2_sql
    assert len(bq.inserts) == 1


def test_verifier_only_writes_to_verdicts_table():
    bq = _FakeBQ(event_rows=[_event_row()], rev_tracker_count=1)
    verify_event("evt-readonly", client=bq)
    assert len(bq.inserts) == 1
    assert bq.inserts[0][0] == "marble-light.operational_intelligence.verdicts"


def test_verdicts_insert_failure_raises():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        insert_errors=[{"index": 0, "errors": [{"reason": "bad"}]}],
    )
    with pytest.raises(RuntimeError, match="verdicts insert failed"):
        verify_event("evt-boom", client=bq)


def test_empty_event_id_raises():
    with pytest.raises(ValueError, match="non-empty str"):
        verify_event("", client=_FakeBQ(event_rows=[]))


def test_non_string_event_id_raises():
    with pytest.raises(ValueError, match="non-empty str"):
        verify_event(123, client=_FakeBQ(event_rows=[]))  # type: ignore[arg-type]


def test_default_client_not_constructed_when_client_injected(monkeypatch):
    def _boom():
        raise AssertionError("_default_client should not be called")

    monkeypatch.setattr(verifier_mod, "_default_client", _boom)
    bq = _FakeBQ(event_rows=[_event_row()], rev_tracker_count=1)
    verify_event("evt-inject", client=bq)


def test_runtime_seconds_populated_and_nonnegative():
    bq = _FakeBQ(event_rows=[_event_row()], rev_tracker_count=1)
    result = verify_event("evt-timing", client=bq)
    assert isinstance(result["runtime_seconds"], float)
    assert result["runtime_seconds"] >= 0.0
    assert isinstance(bq.inserts[0][1][0]["runtime_seconds"], float)


def test_check_1_sql_targets_rev_tracker_and_uses_params():
    bq = _FakeBQ(event_rows=[_event_row()], rev_tracker_count=1)
    result = verify_event("evt-sql", client=bq)
    sql = result["evidence_refs"]["check_1_sql"]
    assert "marble-light.counters.rev_tracker" in sql
    assert "orgName" in sql
    assert "@entity" in sql
    assert "@onset_ts" in sql
    assert "3 DAY" in sql


def test_naive_onset_ts_from_events_is_treated_as_utc():
    bq = _FakeBQ(
        event_rows=[_event_row(onset_ts=datetime(2026, 5, 23))],
        rev_tracker_count=1,
    )
    result = verify_event("evt-naive", client=bq)
    row = bq.inserts[0][1][0]
    assert row["onset_ts"] == "2026-05-23T00:00:00+00:00"
    assert result["verdict"] == "PASS_WITH_CAVEATS"


# ---------------------------------------------------------------------------
# check-2 (persistence) — logic
# ---------------------------------------------------------------------------

def test_check_2_support_when_post_clearly_below_baseline():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        check_2_series=_series(100.0, 50.0),  # 50 <= 90 (=100*0.9)
    )
    result = verify_event("evt-support", client=bq)
    c2 = result["check_results"]["check_2"]
    assert c2["status"] == "SUPPORT"
    assert c2["baseline_median"] == 100.0
    assert c2["post_median"] == 50.0
    assert c2["baseline_n"] == 28
    assert c2["post_n"] == 15


def test_check_2_undermine_on_full_recovery():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        check_2_series=_series(100.0, 100.0),  # 100 > 90
    )
    result = verify_event("evt-recover", client=bq)
    c2 = result["check_results"]["check_2"]
    assert c2["status"] == "UNDERMINE"
    assert c2["post_median"] == 100.0
    assert c2["baseline_median"] == 100.0


def test_check_2_neutral_when_insufficient_post_days():
    # 5 post days, threshold is 7
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        check_2_series=_series(100.0, 50.0, post_days=5),
    )
    result = verify_event("evt-thin", client=bq)
    c2 = result["check_results"]["check_2"]
    assert c2["status"] == "NEUTRAL"
    assert "insufficient post-onset data" in c2["detail"]
    assert c2["post_n"] == 5


def test_check_2_neutral_when_zero_baseline():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        check_2_series=_series(0.0, 50.0),  # baseline median is 0
    )
    result = verify_event("evt-zerobase", client=bq)
    c2 = result["check_results"]["check_2"]
    assert c2["status"] == "NEUTRAL"
    assert "0 or NULL" in c2["detail"]


def test_check_2_neutral_when_query_raises():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        check_2_raises=True,
    )
    result = verify_event("evt-c2boom", client=bq)
    c2 = result["check_results"]["check_2"]
    assert c2["status"] == "NEUTRAL"
    assert c2["errored"] is True
    assert "check 2 errored" in c2["detail"]
    # Fail-closed: verdict row still written.
    assert len(bq.inserts) == 1
    assert result["verdict"] == "PASS_WITH_CAVEATS"
    # SQL was built before execution — evidence_refs preserves the intended
    # query even though the run raised.
    assert "check_2_sql" in result["evidence_refs"]
    assert "rev_tracker" in result["evidence_refs"]["check_2_sql"]
    # And it lands in the verdicts row too.
    parsed_refs = json.loads(bq.inserts[0][1][0]["evidence_refs"])
    assert "check_2_sql" in parsed_refs


def test_check_2_boundary_exactly_at_threshold_is_support():
    # baseline_median = 100 → threshold = 90. post_median exactly 90.
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        check_2_series=_series(100.0, 90.0),
    )
    result = verify_event("evt-boundary", client=bq)
    c2 = result["check_results"]["check_2"]
    assert c2["status"] == "SUPPORT"
    assert c2["post_median"] == 90.0


def test_check_2_rpm_uses_sums_then_divide():
    bq = _FakeBQ(
        event_rows=[_event_row(signal="rpm")],
        rev_tracker_count=1,
        check_2_series=_series(4.0, 2.0),  # arbitrary — assertion is on SQL text
    )
    result = verify_event("evt-rpm", client=bq)
    sql = result["evidence_refs"]["check_2_sql"]
    assert "SAFE_DIVIDE" in sql
    assert "SUM(ProgRev)" in sql
    assert "SUM(DirectRev)" in sql
    assert "NULLIF(SUM(aa_pageviews), 0)" in sql
    assert "* 1000" in sql
    # And absolutely NO average-of-ratios.
    assert "AVG(" not in sql


def test_check_2_sql_targets_rev_tracker_and_uses_params():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        check_2_series=_series(100.0, 50.0),
    )
    result = verify_event("evt-c2sql", client=bq)
    sql = result["evidence_refs"]["check_2_sql"]
    assert "marble-light.counters.rev_tracker" in sql
    assert "orgName" in sql
    assert "@entity" in sql
    assert "@onset_ts" in sql
    assert "28 DAY" in sql
    assert "14 DAY" in sql
    assert "GROUP BY" in sql
    assert "SUM(aa_pageviews)" in sql  # aapv path


# ---------------------------------------------------------------------------
# verdict wiring across check-1 and check-2
# ---------------------------------------------------------------------------

def test_verdict_check1_pass_check2_undermine_is_pass_with_caveats_with_caveat():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=17,
        check_2_series=_series(100.0, 100.0),  # UNDERMINE
    )
    result = verify_event("evt-flakey", client=bq)
    assert result["verdict"] == "PASS_WITH_CAVEATS"
    caveats = result["check_results"]["caveats"]
    assert any(c.startswith("check_2 UNDERMINE") for c in caveats)
    assert "checks 3–7 not yet implemented" in caveats
    assert len(bq.inserts) == 1


def test_verdict_check1_fail_kills_regardless_of_check_2():
    # Even a SUPPORTing / UNDERMINING / NEUTRAL check-2 can't save a KILLed check-1.
    for series in (
        _series(100.0, 50.0),   # would be SUPPORT
        _series(100.0, 100.0),  # would be UNDERMINE
        _series(0.0, 0.0),      # would be NEUTRAL (zero baseline)
    ):
        bq = _FakeBQ(
            event_rows=[_event_row()],
            rev_tracker_count=0,
            check_2_series=series,
        )
        result = verify_event("evt-terminal", client=bq)
        assert result["verdict"] == "KILL"
        assert "caveats" not in result["check_results"]
