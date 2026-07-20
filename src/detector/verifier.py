"""Verifier for the detector portfolio.

verify_event(event_id) reads one row from the events table, runs the
currently-implemented checks against source data in read-only mode, and
appends one verdict row to the verdicts table.

HARD CONSTRAINT: the verifier code path is read-only against source data.
The ONLY write is the single append to the verdicts table. No DML/DDL
anywhere else.

Implemented checks:
  - check 1: data-integrity gate (terminal on FAIL — verdict KILL).
  - check 2: persistence — did the drop persist after onset, or recover?
  - check 6: seasonal twin — is this a recurring annual drop?
Checks 3–5 and 7 are recorded as NOT_IMPLEMENTED until implemented.
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

_BQ_PROJECT = "marble-light"
_EVENTS_TABLE = "marble-light.operational_intelligence.events"
_VERDICTS_TABLE = "marble-light.operational_intelligence.verdicts"
_REV_TRACKER_TABLE = "marble-light.counters.rev_tracker"

_CONFIG_VERSION = "verifier-0.0.3-check6"
_NOT_IMPLEMENTED_CAVEAT = "checks 3–5 and 7 not yet implemented"
_UNIMPLEMENTED_CHECKS = ("check_3", "check_4", "check_5", "check_7")

_REV_TRACKER_ENTITY_COL = "orgName"
_REV_TRACKER_TS_COL = "date"

# ---- Check 2 (persistence) — constants ready for the benchmark to tune ----
BASELINE_DAYS = 28
POST_DAYS = 14
PERSISTENCE_THRESHOLD = 0.10
MIN_POST_DAYS = 7

# ---- Check 6 (seasonal twin) ----
TWIN_LOOKBACK_YEARS = 2
TWIN_WINDOW_TOLERANCE_DAYS = 10

# Detection seasonal baselines learn 2024-01-01+ only. Twin matches whose
# candidate onset lands before this year get flagged pre_regime=true in
# that year's detail so the benchmark can judge whether pre-regime twins
# should count as evidence of expected seasonality.
_REGIME_START_YEAR = 2024

# Per-signal aggregation for the shared series helper. rpm sums revenue
# and pageviews FIRST and divides once — never average per-website RPMs.
# SAFE_DIVIDE already returns NULL on /0, keep NULLIF as belt-and-suspenders.
_SIGNAL_EXPRESSIONS = {
    "aapv": "SUM(aa_pageviews)",
    "rpm": (
        "SAFE_DIVIDE(SUM(ProgRev) + SUM(DirectRev), "
        "NULLIF(SUM(aa_pageviews), 0)) * 1000"
    ),
}


def _default_client():
    from google.cloud import bigquery
    return bigquery.Client(project=_BQ_PROJECT)


def _fetch_event(bq: Any, event_id: str) -> dict:
    from google.cloud import bigquery

    sql = (
        f"SELECT entity, signal, onset_ts FROM `{_EVENTS_TABLE}` "
        "WHERE event_id = @event_id LIMIT 1"
    )
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("event_id", "STRING", event_id),
        ]
    )
    rows = list(bq.query(sql, job_config=job_config).result())
    if not rows:
        raise LookupError(f"event_id not found in events table: {event_id!r}")
    row = rows[0]
    return {
        "entity": row["entity"],
        "signal": row["signal"],
        "onset_ts": row["onset_ts"],
    }


def _run_check_1(bq: Any, entity: str, onset_ts: datetime) -> tuple[dict, str]:
    """Data-integrity gate: does rev_tracker have any rows for this entity
    within ±3 days of onset? Terminal on FAIL."""
    from google.cloud import bigquery

    sql = (
        f"SELECT COUNT(*) AS n FROM `{_REV_TRACKER_TABLE}` "
        f"WHERE {_REV_TRACKER_ENTITY_COL} = @entity "
        f"AND {_REV_TRACKER_TS_COL} "
        "BETWEEN DATE_SUB(DATE(@onset_ts), INTERVAL 3 DAY) "
        "AND DATE_ADD(DATE(@onset_ts), INTERVAL 3 DAY)"
    )
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("entity", "STRING", entity),
            bigquery.ScalarQueryParameter("onset_ts", "TIMESTAMP", onset_ts),
        ]
    )
    rows = list(bq.query(sql, job_config=job_config).result())
    n = int(rows[0]["n"]) if rows else 0
    status = "PASS" if n > 0 else "FAIL"
    return (
        {
            "status": status,
            "detail": f"rev_tracker rows within ±3 days: {n}",
            "rows_found": n,
        },
        sql,
    )


# ---------------------------------------------------------------------------
# Shared per-signal daily-series helper (used by check 2 and check 6)
# ---------------------------------------------------------------------------

def _series_sql_for(signal: str) -> str | None:
    """Build the shared per-org daily-series SQL for `signal`, parameterized
    by @entity, @start_date, and @end_date. Returns None if the signal isn't
    available from rev_tracker.

    Kept separate from execution so callers can record the intended SQL in
    evidence_refs even if the query raises. Check 2 and check 6 use the
    exact same SQL string; only the DATE parameters differ.
    """
    signal_expr = _SIGNAL_EXPRESSIONS.get(signal)
    if signal_expr is None:
        return None
    return (
        f"SELECT {_REV_TRACKER_TS_COL} AS d, {signal_expr} AS value "
        f"FROM `{_REV_TRACKER_TABLE}` "
        f"WHERE {_REV_TRACKER_ENTITY_COL} = @entity "
        f"AND {_REV_TRACKER_TS_COL} BETWEEN @start_date AND @end_date "
        f"GROUP BY {_REV_TRACKER_TS_COL} "
        f"ORDER BY {_REV_TRACKER_TS_COL}"
    )


def _fetch_series(
    bq: Any, entity: str, sql: str, start_date: date, end_date: date,
) -> list:
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("entity", "STRING", entity),
            bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
            bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
        ]
    )
    return list(bq.query(sql, job_config=job_config).result())


def _shift_years_back(d: date, years: int) -> date:
    """Shift a date back by N years. Feb-29 in a non-leap year → Feb-28."""
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(year=d.year - years, day=28)


# ---------------------------------------------------------------------------
# Check 2 — persistence
# ---------------------------------------------------------------------------

def _run_check_2(bq: Any, entity: str, onset_ts: datetime, sql: str) -> dict:
    """Fetch [onset - BASELINE_DAYS, onset + POST_DAYS] and compare medians."""
    onset_day = onset_ts.date()
    start_date = onset_day - timedelta(days=BASELINE_DAYS)
    end_date = onset_day + timedelta(days=POST_DAYS)
    rows = _fetch_series(bq, entity, sql, start_date, end_date)

    baseline = [
        float(r["value"]) for r in rows
        if r["d"] < onset_day and r["value"] is not None
    ]
    post = [
        float(r["value"]) for r in rows
        if r["d"] >= onset_day and r["value"] is not None
    ]

    if len(post) < MIN_POST_DAYS:
        return {
            "status": "NEUTRAL",
            "detail": f"insufficient post-onset data (n={len(post)})",
            "baseline_n": len(baseline),
            "post_n": len(post),
        }

    if not baseline:
        return {
            "status": "NEUTRAL",
            "detail": "baseline_median is 0 or NULL (no baseline rows)",
            "baseline_n": 0,
            "post_n": len(post),
        }

    baseline_median = statistics.median(baseline)
    if baseline_median <= 0:
        return {
            "status": "NEUTRAL",
            "detail": f"baseline_median is 0 or NULL (={baseline_median!r})",
            "baseline_median": baseline_median,
            "baseline_n": len(baseline),
            "post_n": len(post),
        }

    post_median = statistics.median(post)
    threshold_line = baseline_median * (1.0 - PERSISTENCE_THRESHOLD)
    persisted = post_median <= threshold_line
    status = "SUPPORT" if persisted else "UNDERMINE"
    detail = (
        f"post_median={post_median:.4g} "
        f"{'<=' if persisted else '>'} "
        f"baseline_median*(1-{PERSISTENCE_THRESHOLD})={threshold_line:.4g} "
        f"(baseline_median={baseline_median:.4g}, "
        f"baseline_n={len(baseline)}, post_n={len(post)})"
    )
    return {
        "status": status,
        "detail": detail,
        "baseline_median": baseline_median,
        "post_median": post_median,
        "threshold_line": threshold_line,
        "baseline_n": len(baseline),
        "post_n": len(post),
    }


# ---------------------------------------------------------------------------
# Check 6 — seasonal twin
# ---------------------------------------------------------------------------

def _best_twin_candidate(rows: list, twin_onset: date) -> tuple[bool, dict | None]:
    """Slide candidate onsets across twin_onset ± TOLERANCE and return
    (had_evaluable_candidate, best_match_or_None).

    "Best" = lowest post/baseline ratio among candidates that meet the
    persistence threshold; ties break by proximity to twin_onset (a match
    at the exact anniversary date wins over a nearby offset). This keeps
    step-function drops that saturate the tolerance band from producing an
    arbitrary edge candidate. had_evaluable_candidate = True if at least
    one candidate had both a baseline and >= MIN_POST_DAYS of post data.
    """
    best: dict | None = None
    had_evaluable_candidate = False
    for offset in range(-TWIN_WINDOW_TOLERANCE_DAYS, TWIN_WINDOW_TOLERANCE_DAYS + 1):
        candidate = twin_onset + timedelta(days=offset)
        baseline = [
            float(r["value"]) for r in rows
            if (candidate - timedelta(days=BASELINE_DAYS)) <= r["d"] < candidate
            and r["value"] is not None
        ]
        post = [
            float(r["value"]) for r in rows
            if candidate <= r["d"] <= candidate + timedelta(days=POST_DAYS)
            and r["value"] is not None
        ]
        if len(post) < MIN_POST_DAYS or not baseline:
            continue
        baseline_median = statistics.median(baseline)
        if baseline_median <= 0:
            continue
        had_evaluable_candidate = True
        post_median = statistics.median(post)
        threshold_line = baseline_median * (1.0 - PERSISTENCE_THRESHOLD)
        if post_median > threshold_line:
            continue
        ratio = post_median / baseline_median
        distance = abs(offset)
        if (
            best is None
            or ratio < best["ratio"]
            or (ratio == best["ratio"] and distance < best["distance"])
        ):
            best = {
                "candidate_onset": candidate,
                "ratio": ratio,
                "distance": distance,
                "baseline_median": baseline_median,
                "post_median": post_median,
            }
    if best is not None:
        best.pop("distance", None)  # internal tie-breaker; not part of the reported detail
    return had_evaluable_candidate, best


def _run_check_6(bq: Any, entity: str, onset_ts: datetime, sql: str) -> dict:
    """Look for a matching drop in the same calendar position of prior years.

    A twin exists in year y if any candidate onset in [twin_onset ±
    TOLERANCE] meets the persistence threshold with enough post data.
    """
    onset_day = onset_ts.date()
    year_details: list[dict] = []
    years_with_data = 0
    matches: list[dict] = []

    for y in range(1, TWIN_LOOKBACK_YEARS + 1):
        twin_onset = _shift_years_back(onset_day, y)
        start_date = twin_onset - timedelta(
            days=BASELINE_DAYS + TWIN_WINDOW_TOLERANCE_DAYS
        )
        end_date = twin_onset + timedelta(
            days=POST_DAYS + TWIN_WINDOW_TOLERANCE_DAYS
        )
        rows = _fetch_series(bq, entity, sql, start_date, end_date)
        had_data, best = _best_twin_candidate(rows, twin_onset)

        detail: dict = {
            "year_offset": y,
            "twin_onset": twin_onset.isoformat(),
            "has_data": had_data,
            "twin_found": best is not None,
        }
        if had_data:
            years_with_data += 1
        if best is not None:
            detail["candidate_onset"] = best["candidate_onset"].isoformat()
            detail["ratio"] = best["ratio"]
            detail["baseline_median"] = best["baseline_median"]
            detail["post_median"] = best["post_median"]
            if best["candidate_onset"].year < _REGIME_START_YEAR:
                detail["pre_regime"] = True
            matches.append(detail)
        year_details.append(detail)

    if matches:
        summary = ", ".join(
            f"year-{m['year_offset']}@{m['candidate_onset']}(ratio={m['ratio']:.2g})"
            for m in matches
        )
        return {
            "status": "UNDERMINE",
            "detail": f"twin drop found in {len(matches)} prior year(s): {summary}",
            "years": year_details,
        }
    if years_with_data == 0:
        return {
            "status": "NEUTRAL",
            "detail": "no prior-year data available for twin lookup",
            "years": year_details,
        }
    return {
        "status": "SUPPORT",
        "detail": f"no comparable twin drop in {years_with_data} prior year(s) with data",
        "years": year_details,
    }


# ---------------------------------------------------------------------------
# Verdict wiring
# ---------------------------------------------------------------------------

def _classify_vote(status: str) -> str | None:
    """Map a check status to a SUPPORT/UNDERMINE vote (or None if non-voting).

    Check-1's PASS/FAIL and check-2 / check-6's SUPPORT/UNDERMINE feed the
    same tally so the "≥2 UNDERMINE and 0 SUPPORT → KILL" rule can be
    evaluated generally across implemented checks.

    BENCHMARK REVIEW: counting check_1 PASS as a SUPPORT vote is a deliberate
    choice — it keeps the KILL branch inert until more checks land. If the
    benchmark shows we're under-killing recovered/transient drops, revisit
    whether check_1 should be gate-only (terminal on FAIL, non-voting on PASS)
    so a chorus of UNDERMINE votes from other checks can trigger KILL.
    """
    if status in ("PASS", "SUPPORT"):
        return "SUPPORT"
    if status in ("FAIL", "UNDERMINE"):
        return "UNDERMINE"
    return None


def _decide_verdict(check_1: dict, other_checks: list[dict]) -> str:
    """Verdict contract:
      check_1 FAIL → KILL (terminal — data-integrity gate).
      Else if ≥2 UNDERMINE and 0 SUPPORT across implemented checks → KILL.
      Else PASS_WITH_CAVEATS.
    """
    if check_1["status"] == "FAIL":
        return "KILL"
    votes = [_classify_vote(c["status"]) for c in [check_1, *other_checks]]
    if votes.count("UNDERMINE") >= 2 and votes.count("SUPPORT") == 0:
        return "KILL"
    return "PASS_WITH_CAVEATS"


def _build_caveats(implemented_named: list[tuple[str, dict]]) -> list[str]:
    """Any NEUTRAL/UNDERMINE implemented check → caveat with its detail;
    plus the standing 'checks 3–5 and 7 not yet implemented' note."""
    caveats: list[str] = []
    for name, result in implemented_named:
        if result["status"] in ("NEUTRAL", "UNDERMINE"):
            detail = result.get("detail", "")
            caveats.append(f"{name} {result['status']}: {detail}")
    caveats.append(_NOT_IMPLEMENTED_CAVEAT)
    return caveats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _safely(runner, on_error_prefix: str) -> dict:
    """Run `runner()`; on exception return a NEUTRAL result with the SQL
    still recorded upstream (fail-closed)."""
    try:
        return runner()
    except Exception as e:
        return {
            "status": "NEUTRAL",
            "detail": f"{on_error_prefix} errored: {type(e).__name__}: {e}",
            "errored": True,
        }


def verify_event(event_id: str, *, client: Any = None) -> dict:
    """Read one event, run implemented checks, write one verdict row.

    Returns a dict describing the verdict (event_id, verdict, check_results,
    evidence_refs, config_version, runtime_seconds).

    Raises LookupError if the event_id is not present in the events table.
    Raises RuntimeError if the verdicts insert reports errors.
    """
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id must be a non-empty str")

    bq = client if client is not None else _default_client()
    started = time.monotonic()

    event = _fetch_event(bq, event_id)
    onset_ts: datetime = event["onset_ts"]
    if onset_ts.tzinfo is None:
        onset_ts = onset_ts.replace(tzinfo=timezone.utc)

    check_1_result, check_1_sql = _run_check_1(bq, event["entity"], onset_ts)

    # Both check 2 and check 6 share the same per-signal daily-series SQL —
    # build it once, up front, so evidence_refs records the intended query
    # even if execution raises (fail-closed logging).
    series_sql = _series_sql_for(event["signal"])
    if series_sql is None:
        unsupported = {
            "status": "NEUTRAL",
            "detail": f"signal {event['signal']!r} not supported by rev_tracker",
        }
        check_2_result = dict(unsupported)
        check_6_result = dict(unsupported)
    else:
        check_2_result = _safely(
            lambda: _run_check_2(bq, event["entity"], onset_ts, series_sql),
            "check 2",
        )
        check_6_result = _safely(
            lambda: _run_check_6(bq, event["entity"], onset_ts, series_sql),
            "check 6",
        )

    check_results: dict = {
        "check_1": check_1_result,
        "check_2": check_2_result,
        "check_6": check_6_result,
    }
    for name in _UNIMPLEMENTED_CHECKS:
        check_results[name] = {"status": "NOT_IMPLEMENTED"}

    verdict = _decide_verdict(check_1_result, [check_2_result, check_6_result])

    if verdict == "PASS_WITH_CAVEATS":
        check_results["caveats"] = _build_caveats([
            ("check_1", check_1_result),
            ("check_2", check_2_result),
            ("check_6", check_6_result),
        ])

    evidence_refs = {"check_1_sql": check_1_sql}
    if series_sql is not None:
        # Same SQL string powers both checks; record under both names for
        # clarity in evidence_refs (they run against different DATE params).
        evidence_refs["check_2_sql"] = series_sql
        evidence_refs["check_6_sql"] = series_sql

    runtime_seconds = time.monotonic() - started

    verdict_row = {
        "event_id": event_id,
        "onset_ts": onset_ts.astimezone(timezone.utc).isoformat(),
        "verdict": verdict,
        "check_results": json.dumps(check_results),
        "evidence_refs": json.dumps(evidence_refs),
        "brief_md": None,
        "runtime_seconds": runtime_seconds,
        "config_version": _CONFIG_VERSION,
        "verdict_ts": datetime.now(timezone.utc).isoformat(),
    }

    errors = bq.insert_rows_json(_VERDICTS_TABLE, [verdict_row])
    if errors:
        raise RuntimeError(f"BigQuery verdicts insert failed: {errors}")

    return {
        "event_id": event_id,
        "verdict": verdict,
        "check_results": check_results,
        "evidence_refs": evidence_refs,
        "config_version": _CONFIG_VERSION,
        "runtime_seconds": runtime_seconds,
    }
