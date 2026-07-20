"""Tests for detector.verifier.verify_event — checks 1, 2, 6. BigQuery mocked.

Coverage:
  Check 1 (data-integrity gate, terminal on FAIL) — tracer cases carried over.
  Check 2 (persistence) — SUPPORT, UNDERMINE, three NEUTRAL paths, boundary,
    RPM sums-then-divide aggregation.
  Check 6 (seasonal twin) — UNDERMINE (exact), UNDERMINE via tolerance,
    SUPPORT (no twin), two NEUTRAL paths (no data, query raises),
    pre_regime flag, shared-SQL regression.
  Verdict wiring across the three checks; hard read-only constraint.

All BigQuery I/O is stubbed via a fake client — no ADC, no network.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from detector import verify_event
from detector import verifier as verifier_mod
from detector.verifier import (
    BASELINE_DAYS,
    POST_DAYS,
    TWIN_WINDOW_TOLERANCE_DAYS,
)


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
    """Routes SQL by substring; series queries are further routed by window
    span so we can distinguish check 2 (span = BASELINE_DAYS + POST_DAYS)
    from check 6 (span = ... + 2 * TWIN_WINDOW_TOLERANCE_DAYS) and by
    @start_date's year (for per-year check-6 series).

      - "COUNT(*)"                                    -> check 1 (count row)
      - "rev_tracker" + span 42                        -> check 2 series
      - "rev_tracker" + span 62                        -> check 6 series (year-routed)
      - "operational_intelligence.events"              -> event fetch
    """

    def __init__(
        self,
        *,
        event_rows,
        rev_tracker_count=0,
        default_series=None,
        series_by_year=None,
        check_2_raises=False,
        check_6_raises=False,
        insert_errors=None,
    ):
        self._event_rows = event_rows
        self._rev_tracker_count = rev_tracker_count
        self._default_series = default_series if default_series is not None else []
        self._series_by_year = series_by_year or {}
        self._check_2_raises = check_2_raises
        self._check_6_raises = check_6_raises
        self._insert_errors = insert_errors or []
        self.queries = []
        self.inserts = []

    def query(self, sql, job_config=None):
        self.queries.append((sql, job_config))
        if "COUNT(*)" in sql:
            return _FakeQueryJob([{"n": self._rev_tracker_count}])
        if "operational_intelligence.events" in sql:
            return _FakeQueryJob(self._event_rows)
        if "rev_tracker" in sql:
            params = {
                p.name: p.value
                for p in (job_config.query_parameters if job_config else [])
            }
            span = (params["end_date"] - params["start_date"]).days
            if span == BASELINE_DAYS + POST_DAYS:
                if self._check_2_raises:
                    raise RuntimeError("simulated check-2 BQ error")
                return _FakeQueryJob(self._default_series)
            # else: check 6 (wider window)
            if self._check_6_raises:
                raise RuntimeError("simulated check-6 BQ error")
            year_rows = self._series_by_year.get(params["start_date"].year, [])
            return _FakeQueryJob(year_rows)
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
    baseline_days=BASELINE_DAYS,
    post_days=POST_DAYS + 1,   # onset day + 14 more = 15 inclusive rows
    onset=_DEFAULT_ONSET_DATE,
):
    """Build a synthetic rev_tracker daily series for check 2's window
    [onset-BASELINE_DAYS, onset+POST_DAYS]. Values may be None."""
    rows = []
    for i in range(baseline_days, 0, -1):
        rows.append({"d": onset - timedelta(days=i), "value": baseline_val})
    for i in range(post_days):
        rows.append({"d": onset + timedelta(days=i), "value": post_val})
    return rows


def _twin_series(
    baseline_val,
    post_val,
    *,
    anchor,
    offset_from_anchor=0,
):
    """Build a wide daily series covering check 6's fetch window centered on
    `anchor` (the twin_onset for a specific prior year).

    Values are `baseline_val` for dates before `anchor + offset_from_anchor`
    and `post_val` from that date onward. Use offset_from_anchor to place
    the drop away from the exact calendar date and exercise the ± tolerance
    matcher.
    """
    transition = anchor + timedelta(days=offset_from_anchor)
    days_before = BASELINE_DAYS + TWIN_WINDOW_TOLERANCE_DAYS  # 38
    days_after = POST_DAYS + TWIN_WINDOW_TOLERANCE_DAYS       # 24
    rows = []
    for i in range(days_before, 0, -1):
        d = anchor - timedelta(days=i)
        rows.append({
            "d": d,
            "value": baseline_val if d < transition else post_val,
        })
    for i in range(days_after + 1):
        d = anchor + timedelta(days=i)
        rows.append({
            "d": d,
            "value": baseline_val if d < transition else post_val,
        })
    return rows


def _no_twin_default_years():
    """Flat (no-drop) prior-year series for both lookback years — yields
    check_6 SUPPORT and keeps caveat assertions clean in check-2 focused
    tests."""
    return {
        2025: _twin_series(100.0, 100.0, anchor=date(2025, 5, 23)),
        2024: _twin_series(100.0, 100.0, anchor=date(2024, 5, 23)),
    }


# ---------------------------------------------------------------------------
# check-1 tracer cases (updated for the new verdict wiring)
# ---------------------------------------------------------------------------

def test_check_1_pass_and_check_2_support_yields_pass_with_caveats():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=17,
        default_series=_series(100.0, 50.0),   # check 2 SUPPORT
        series_by_year=_no_twin_default_years(),  # check 6 SUPPORT
    )

    result = verify_event("evt-123", client=bq)

    assert result["verdict"] == "PASS_WITH_CAVEATS"
    assert result["check_results"]["check_1"]["status"] == "PASS"
    assert result["check_results"]["check_1"]["rows_found"] == 17
    assert result["check_results"]["check_2"]["status"] == "SUPPORT"
    assert result["check_results"]["check_6"]["status"] == "SUPPORT"
    for name in ("check_3", "check_4", "check_5", "check_7"):
        assert result["check_results"][name] == {"status": "NOT_IMPLEMENTED"}
    assert result["check_results"]["caveats"] == [
        "checks 3–5 and 7 not yet implemented"
    ]
    assert result["config_version"] == "verifier-0.0.3-check6"

    assert len(bq.inserts) == 1
    table, rows = bq.inserts[0]
    assert table == "marble-light.operational_intelligence.verdicts"
    row = rows[0]
    assert row["event_id"] == "evt-123"
    assert row["verdict"] == "PASS_WITH_CAVEATS"
    assert row["config_version"] == "verifier-0.0.3-check6"
    assert row["brief_md"] is None
    assert row["onset_ts"] == "2026-05-23T00:00:00+00:00"

    parsed_checks = json.loads(row["check_results"])
    assert parsed_checks["check_1"]["status"] == "PASS"
    assert parsed_checks["check_2"]["status"] == "SUPPORT"
    assert parsed_checks["check_6"]["status"] == "SUPPORT"
    assert parsed_checks["caveats"] == ["checks 3–5 and 7 not yet implemented"]

    parsed_refs = json.loads(row["evidence_refs"])
    assert "check_1_sql" in parsed_refs
    assert "check_2_sql" in parsed_refs
    assert "check_6_sql" in parsed_refs
    # Check 2 and check 6 share the same SQL string; different DATE params.
    assert parsed_refs["check_2_sql"] == parsed_refs["check_6_sql"]


def test_check_1_fail_yields_kill():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=0,
        default_series=_series(100.0, 50.0),
        series_by_year=_no_twin_default_years(),
    )

    result = verify_event("evt-456", client=bq)

    assert result["verdict"] == "KILL"
    assert result["check_results"]["check_1"]["status"] == "FAIL"
    assert result["check_results"]["check_1"]["rows_found"] == 0
    assert "caveats" not in result["check_results"]

    assert len(bq.inserts) == 1
    row = bq.inserts[0][1][0]
    assert row["verdict"] == "KILL"


def test_event_not_found_raises_and_writes_nothing():
    bq = _FakeBQ(event_rows=[])

    with pytest.raises(LookupError, match="event_id not found"):
        verify_event("evt-missing", client=bq)

    assert bq.inserts == []
    assert all("rev_tracker" not in sql for sql, _ in bq.queries)


# ---------------------------------------------------------------------------
# structural / hard-constraint guarantees
# ---------------------------------------------------------------------------

def test_events_then_check1_then_series_queries_then_verdicts_insert():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=5,
        default_series=_series(100.0, 50.0),
        series_by_year=_no_twin_default_years(),
    )
    verify_event("evt-order", client=bq)

    # events fetch, check 1 COUNT, check 2 series, check 6 year 1, check 6 year 2
    assert len(bq.queries) == 5
    assert "operational_intelligence.events" in bq.queries[0][0]
    assert "COUNT(*)" in bq.queries[1][0]
    for sql, _ in bq.queries[2:5]:
        assert "rev_tracker" in sql and "COUNT(*)" not in sql
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
        default_series=_series(100.0, 50.0),
    )
    result = verify_event("evt-support", client=bq)
    c2 = result["check_results"]["check_2"]
    assert c2["status"] == "SUPPORT"
    assert c2["baseline_median"] == 100.0
    assert c2["post_median"] == 50.0
    assert c2["baseline_n"] == BASELINE_DAYS
    assert c2["post_n"] == POST_DAYS + 1


def test_check_2_undermine_on_full_recovery():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        default_series=_series(100.0, 100.0),
    )
    result = verify_event("evt-recover", client=bq)
    c2 = result["check_results"]["check_2"]
    assert c2["status"] == "UNDERMINE"
    assert c2["post_median"] == 100.0
    assert c2["baseline_median"] == 100.0


def test_check_2_neutral_when_insufficient_post_days():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        default_series=_series(100.0, 50.0, post_days=5),
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
        default_series=_series(0.0, 50.0),
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
    assert len(bq.inserts) == 1
    assert result["verdict"] == "PASS_WITH_CAVEATS"
    # SQL was built before execution — evidence_refs preserves it.
    assert "check_2_sql" in result["evidence_refs"]
    assert "rev_tracker" in result["evidence_refs"]["check_2_sql"]
    parsed_refs = json.loads(bq.inserts[0][1][0]["evidence_refs"])
    assert "check_2_sql" in parsed_refs


def test_check_2_boundary_exactly_at_threshold_is_support():
    # baseline_median = 100 → threshold = 90. post_median exactly 90.
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        default_series=_series(100.0, 90.0),
    )
    result = verify_event("evt-boundary", client=bq)
    c2 = result["check_results"]["check_2"]
    assert c2["status"] == "SUPPORT"
    assert c2["post_median"] == 90.0


def test_check_2_rpm_uses_sums_then_divide():
    bq = _FakeBQ(
        event_rows=[_event_row(signal="rpm")],
        rev_tracker_count=1,
        default_series=_series(4.0, 2.0),
    )
    result = verify_event("evt-rpm", client=bq)
    sql = result["evidence_refs"]["check_2_sql"]
    assert "SAFE_DIVIDE" in sql
    assert "SUM(ProgRev)" in sql
    assert "SUM(DirectRev)" in sql
    assert "NULLIF(SUM(aa_pageviews), 0)" in sql
    assert "* 1000" in sql
    assert "AVG(" not in sql


def test_check_2_sql_targets_rev_tracker_with_date_params():
    """Refactored series SQL uses @start_date/@end_date rather than
    @onset_ts + DATE_SUB/DATE_ADD intervals — check 2 and check 6 both use
    the same helper."""
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        default_series=_series(100.0, 50.0),
    )
    result = verify_event("evt-c2sql", client=bq)
    sql = result["evidence_refs"]["check_2_sql"]
    assert "marble-light.counters.rev_tracker" in sql
    assert "orgName" in sql
    assert "@entity" in sql
    assert "@start_date" in sql
    assert "@end_date" in sql
    assert "GROUP BY" in sql
    assert "SUM(aa_pageviews)" in sql
    # The shared helper does NOT hardcode day intervals in the SQL any more.
    assert "INTERVAL 28 DAY" not in sql
    assert "INTERVAL 14 DAY" not in sql


# ---------------------------------------------------------------------------
# check-6 (seasonal twin) — logic
# ---------------------------------------------------------------------------

def test_check_6_undermine_with_exact_twin_in_prior_year():
    bq = _FakeBQ(
        event_rows=[_event_row()],  # onset 2026-05-23
        rev_tracker_count=1,
        default_series=_series(100.0, 50.0),
        series_by_year={
            2025: _twin_series(100.0, 50.0, anchor=date(2025, 5, 23)),
            2024: _twin_series(100.0, 50.0, anchor=date(2024, 5, 23)),
        },
    )
    result = verify_event("evt-twin-exact", client=bq)
    c6 = result["check_results"]["check_6"]
    assert c6["status"] == "UNDERMINE"
    assert "twin drop found" in c6["detail"]
    # Both prior years matched at the exact calendar date.
    matched = [y for y in c6["years"] if y.get("twin_found")]
    assert len(matched) == 2
    for m in matched:
        assert m["ratio"] == 0.5
        # candidate onset lands on the exact anchor day when there's no offset
        assert m["candidate_onset"] in ("2025-05-23", "2024-05-23")


def test_check_6_undermine_via_tolerance_offset():
    """Drop offset by 8 days from the calendar anniversary still matches
    within the ±TWIN_WINDOW_TOLERANCE_DAYS band."""
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        default_series=_series(100.0, 50.0),
        series_by_year={
            2025: _twin_series(
                100.0, 50.0, anchor=date(2025, 5, 23), offset_from_anchor=8,
            ),
            2024: _twin_series(100.0, 100.0, anchor=date(2024, 5, 23)),
        },
    )
    result = verify_event("evt-twin-tolerance", client=bq)
    c6 = result["check_results"]["check_6"]
    assert c6["status"] == "UNDERMINE"
    # Year 1 matched somewhere in the ±TOLERANCE band around 2025-05-23.
    # A step-function drop satisfies multiple candidates; proximity
    # tie-breaking will land on the closest-to-anchor match. The important
    # property is the match sits inside the tolerance band and outside
    # offset 0 (which would mean the drop was AT the anniversary date).
    year_1 = next(y for y in c6["years"] if y["year_offset"] == 1)
    assert year_1["twin_found"] is True
    anchor = date(2025, 5, 23)
    candidate = date.fromisoformat(year_1["candidate_onset"])
    offset = (candidate - anchor).days
    assert 1 <= offset <= TWIN_WINDOW_TOLERANCE_DAYS
    year_2 = next(y for y in c6["years"] if y["year_offset"] == 2)
    assert year_2["twin_found"] is False


def test_check_6_support_when_prior_years_have_data_but_no_matching_drop():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        default_series=_series(100.0, 100.0),
        series_by_year=_no_twin_default_years(),  # flat, no drop
    )
    result = verify_event("evt-no-twin", client=bq)
    c6 = result["check_results"]["check_6"]
    assert c6["status"] == "SUPPORT"
    assert "no comparable twin drop" in c6["detail"]
    for year in c6["years"]:
        assert year["has_data"] is True
        assert year["twin_found"] is False


def test_check_6_neutral_when_no_prior_year_data():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        default_series=_series(100.0, 50.0),
        # series_by_year not provided → both years return []
    )
    result = verify_event("evt-nodata", client=bq)
    c6 = result["check_results"]["check_6"]
    assert c6["status"] == "NEUTRAL"
    assert "no prior-year data" in c6["detail"]


def test_check_6_neutral_when_query_raises():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        default_series=_series(100.0, 50.0),
        check_6_raises=True,
    )
    result = verify_event("evt-c6boom", client=bq)
    c6 = result["check_results"]["check_6"]
    assert c6["status"] == "NEUTRAL"
    assert c6["errored"] is True
    assert "check 6 errored" in c6["detail"]
    # SQL still recorded up front.
    assert "check_6_sql" in result["evidence_refs"]
    assert "rev_tracker" in result["evidence_refs"]["check_6_sql"]


def test_check_6_flags_pre_regime_twin():
    """Event onset 2024-05-23 → twin year 1 = 2023-05-23, which predates
    the 2024-01-01 regime cutoff. Match should carry pre_regime=true."""
    bq = _FakeBQ(
        event_rows=[_event_row(
            onset_ts=datetime(2024, 5, 23, tzinfo=timezone.utc),
        )],
        rev_tracker_count=1,
        default_series=[],  # check 2 doesn't matter for this test
        series_by_year={
            2023: _twin_series(100.0, 50.0, anchor=date(2023, 5, 23)),
            2022: _twin_series(100.0, 50.0, anchor=date(2022, 5, 23)),
        },
    )
    result = verify_event("evt-pre-regime", client=bq)
    c6 = result["check_results"]["check_6"]
    assert c6["status"] == "UNDERMINE"
    for year in c6["years"]:
        assert year["twin_found"] is True
        assert year.get("pre_regime") is True


def test_check_6_sql_matches_check_2_sql():
    """DRY regression: the shared helper means check 2 and check 6 record
    literally the same SQL string in evidence_refs."""
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=1,
        default_series=_series(100.0, 50.0),
        series_by_year=_no_twin_default_years(),
    )
    result = verify_event("evt-shared-sql", client=bq)
    assert result["evidence_refs"]["check_2_sql"] == result["evidence_refs"]["check_6_sql"]


def test_check_6_leap_day_shifts_to_feb_28():
    """Feb-29 onset in a leap year: prior-year twin_onset falls back to
    Feb-28 (avoids raising on invalid dates)."""
    bq = _FakeBQ(
        event_rows=[_event_row(
            onset_ts=datetime(2024, 2, 29, tzinfo=timezone.utc),
        )],
        rev_tracker_count=1,
        default_series=[],
        series_by_year={
            2023: _twin_series(100.0, 100.0, anchor=date(2023, 2, 28)),
            2022: _twin_series(100.0, 100.0, anchor=date(2022, 2, 28)),
        },
    )
    result = verify_event("evt-leap", client=bq)
    c6 = result["check_results"]["check_6"]
    twin_onsets = [y["twin_onset"] for y in c6["years"]]
    assert twin_onsets == ["2023-02-28", "2022-02-28"]


# ---------------------------------------------------------------------------
# verdict wiring across check-1, check-2, check-6
# ---------------------------------------------------------------------------

def test_verdict_check1_pass_check2_undermine_is_pass_with_caveats_with_caveat():
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=17,
        default_series=_series(100.0, 100.0),   # check 2 UNDERMINE
        series_by_year=_no_twin_default_years(),  # check 6 SUPPORT
    )
    result = verify_event("evt-flakey", client=bq)
    assert result["verdict"] == "PASS_WITH_CAVEATS"
    caveats = result["check_results"]["caveats"]
    assert any(c.startswith("check_2 UNDERMINE") for c in caveats)
    assert "checks 3–5 and 7 not yet implemented" in caveats
    assert len(bq.inserts) == 1


def test_verdict_check2_undermine_plus_check6_undermine_still_pass_with_caveats():
    """BENCHMARK REVIEW: check_1 PASS counts as SUPPORT, so 2 UNDERMINE
    votes still don't trigger KILL. The rule is generalized but the current
    KILL branch stays inert with checks 1+2+6."""
    bq = _FakeBQ(
        event_rows=[_event_row()],
        rev_tracker_count=17,
        default_series=_series(100.0, 100.0),  # check 2 UNDERMINE
        series_by_year={
            2025: _twin_series(100.0, 50.0, anchor=date(2025, 5, 23)),  # check 6 UNDERMINE
            2024: _twin_series(100.0, 50.0, anchor=date(2024, 5, 23)),
        },
    )
    result = verify_event("evt-both-undermine", client=bq)
    assert result["verdict"] == "PASS_WITH_CAVEATS"
    caveats = result["check_results"]["caveats"]
    assert any(c.startswith("check_2 UNDERMINE") for c in caveats)
    assert any(c.startswith("check_6 UNDERMINE") for c in caveats)


def test_verdict_check1_fail_kills_regardless_of_check_2_and_check_6():
    for series in (
        _series(100.0, 50.0),
        _series(100.0, 100.0),
        _series(0.0, 0.0),
    ):
        for year_series in (
            _no_twin_default_years(),
            {
                2025: _twin_series(100.0, 50.0, anchor=date(2025, 5, 23)),
                2024: _twin_series(100.0, 50.0, anchor=date(2024, 5, 23)),
            },
            {},
        ):
            bq = _FakeBQ(
                event_rows=[_event_row()],
                rev_tracker_count=0,
                default_series=series,
                series_by_year=year_series,
            )
            result = verify_event("evt-terminal", client=bq)
            assert result["verdict"] == "KILL"
            assert "caveats" not in result["check_results"]
