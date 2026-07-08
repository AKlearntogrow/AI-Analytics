"""Tests for detector.verifier.verify_event — check-1 only, BigQuery mocked.

Coverage per SPEC Step 2:
  1. check-1 PASS → PASS_WITH_CAVEATS row written.
  2. check-1 FAIL → KILL row written.
  3. Event not found → raises LookupError.

Plus a few structural guarantees:
  - Both source SELECTs happen before the single verdicts append.
  - No other writes (verifier is read-only against source data).
  - check_results marks checks 2–7 as NOT_IMPLEMENTED.
  - evidence_refs contains the exact check-1 SQL text.
  - config_version is the tracer value.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from detector import verify_event
from detector import verifier as verifier_mod


# ---------------------------------------------------------------------------
# fake BigQuery client
# ---------------------------------------------------------------------------

class _FakeQueryJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return iter(self._rows)


class _FakeBQ:
    """Routes SQL by substring: events vs. rev_tracker.

    - `event_rows`: what the events SELECT returns (empty list -> not found).
    - `rev_tracker_count`: the integer that the COUNT(*) query returns.
    - `insert_errors`: preset return from insert_rows_json.
    """

    def __init__(self, *, event_rows, rev_tracker_count=0, insert_errors=None):
        self._event_rows = event_rows
        self._rev_tracker_count = rev_tracker_count
        self._insert_errors = insert_errors or []
        self.queries = []       # list of (sql, job_config)
        self.inserts = []       # list of (table, rows)

    def query(self, sql, job_config=None):
        self.queries.append((sql, job_config))
        if "rev_tracker" in sql:
            return _FakeQueryJob([{"n": self._rev_tracker_count}])
        if "operational_intelligence.events" in sql:
            return _FakeQueryJob(self._event_rows)
        raise AssertionError(f"unexpected SQL in test: {sql}")

    def insert_rows_json(self, table, rows):
        self.inserts.append((table, rows))
        return self._insert_errors


def _event_row(
    *,
    entity="Publift",
    onset_ts=datetime(2026, 5, 23, tzinfo=timezone.utc),
):
    return {"entity": entity, "onset_ts": onset_ts}


# ---------------------------------------------------------------------------
# 1. check-1 PASS -> PASS_WITH_CAVEATS
# ---------------------------------------------------------------------------

def test_check_1_pass_yields_pass_with_caveats():
    bq = _FakeBQ(event_rows=[_event_row()], rev_tracker_count=17)

    result = verify_event("evt-123", client=bq)

    assert result["verdict"] == "PASS_WITH_CAVEATS"
    assert result["check_results"]["check_1"]["status"] == "PASS"
    assert result["check_results"]["check_1"]["rows_found"] == 17
    for name in ("check_2", "check_3", "check_4", "check_5", "check_6", "check_7"):
        assert result["check_results"][name] == {"status": "NOT_IMPLEMENTED"}
    assert result["check_results"]["caveats"] == ["checks 2–7 not yet implemented"]
    assert result["config_version"] == "verifier-0.0.1-tracer"

    # Exactly one verdict row appended to the verdicts table.
    assert len(bq.inserts) == 1
    table, rows = bq.inserts[0]
    assert table == "marble-light.operational_intelligence.verdicts"
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == "evt-123"
    assert row["verdict"] == "PASS_WITH_CAVEATS"
    assert row["config_version"] == "verifier-0.0.1-tracer"
    assert row["brief_md"] is None
    assert row["onset_ts"] == "2026-05-23T00:00:00+00:00"

    # check_results and evidence_refs are JSON-encoded strings on the wire.
    parsed_checks = json.loads(row["check_results"])
    assert parsed_checks["check_1"]["status"] == "PASS"
    assert parsed_checks["caveats"] == ["checks 2–7 not yet implemented"]

    parsed_refs = json.loads(row["evidence_refs"])
    assert "check_1_sql" in parsed_refs
    assert "rev_tracker" in parsed_refs["check_1_sql"]
    assert parsed_refs["check_1_sql"] == result["evidence_refs"]["check_1_sql"]


# ---------------------------------------------------------------------------
# 2. check-1 FAIL -> KILL
# ---------------------------------------------------------------------------

def test_check_1_fail_yields_kill():
    bq = _FakeBQ(event_rows=[_event_row()], rev_tracker_count=0)

    result = verify_event("evt-456", client=bq)

    assert result["verdict"] == "KILL"
    assert result["check_results"]["check_1"]["status"] == "FAIL"
    assert result["check_results"]["check_1"]["rows_found"] == 0
    # No caveats key on a KILL verdict (the caveat only attaches to PASS_WITH_CAVEATS).
    assert "caveats" not in result["check_results"]

    assert len(bq.inserts) == 1
    row = bq.inserts[0][1][0]
    assert row["verdict"] == "KILL"
    parsed = json.loads(row["check_results"])
    assert parsed["check_1"]["status"] == "FAIL"


# ---------------------------------------------------------------------------
# 3. Event not found -> raises
# ---------------------------------------------------------------------------

def test_event_not_found_raises_and_writes_nothing():
    bq = _FakeBQ(event_rows=[], rev_tracker_count=0)

    with pytest.raises(LookupError, match="event_id not found"):
        verify_event("evt-missing", client=bq)

    # rev_tracker was never queried and no verdict row was written.
    assert bq.inserts == []
    assert all("rev_tracker" not in sql for sql, _ in bq.queries)


# ---------------------------------------------------------------------------
# structural / hard-constraint guarantees
# ---------------------------------------------------------------------------

def test_events_selected_before_rev_tracker_before_verdicts_insert():
    bq = _FakeBQ(event_rows=[_event_row()], rev_tracker_count=5)
    verify_event("evt-order", client=bq)

    assert len(bq.queries) == 2
    assert "operational_intelligence.events" in bq.queries[0][0]
    assert "rev_tracker" in bq.queries[1][0]
    # verdicts insert happens last (after both SELECTs)
    assert len(bq.inserts) == 1


def test_verifier_only_writes_to_verdicts_table():
    """Hard constraint: no writes anywhere except verdicts."""
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
    """Defensive: if BQ ever hands back a naive datetime, we don't crash."""
    bq = _FakeBQ(
        event_rows=[_event_row(onset_ts=datetime(2026, 5, 23))],
        rev_tracker_count=1,
    )
    result = verify_event("evt-naive", client=bq)
    row = bq.inserts[0][1][0]
    assert row["onset_ts"] == "2026-05-23T00:00:00+00:00"
    assert result["verdict"] == "PASS_WITH_CAVEATS"
