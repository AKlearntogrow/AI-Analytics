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
Checks 3–7 are recorded as NOT_IMPLEMENTED until implemented.
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import datetime, timezone
from typing import Any

_BQ_PROJECT = "marble-light"
_EVENTS_TABLE = "marble-light.operational_intelligence.events"
_VERDICTS_TABLE = "marble-light.operational_intelligence.verdicts"
_REV_TRACKER_TABLE = "marble-light.counters.rev_tracker"

_CONFIG_VERSION = "verifier-0.0.2-check2"
_NOT_IMPLEMENTED_CAVEAT = "checks 3–7 not yet implemented"
_UNIMPLEMENTED_CHECKS = (
    "check_3", "check_4", "check_5", "check_6", "check_7",
)

_REV_TRACKER_ENTITY_COL = "orgName"
_REV_TRACKER_TS_COL = "date"

# ---- Check 2 (persistence) — constants ready for the benchmark to tune ----
BASELINE_DAYS = 28
POST_DAYS = 14
PERSISTENCE_THRESHOLD = 0.10
MIN_POST_DAYS = 7

# Per-signal aggregation for check 2. rpm sums revenue and pageviews FIRST
# and divides once — never average per-website RPMs. SAFE_DIVIDE already
# returns NULL on /0, but keep NULLIF as belt-and-suspenders per spec.
_CHECK_2_SIGNAL_EXPRESSIONS = {
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


def _check_2_sql_for(signal: str) -> str | None:
    """Build the check-2 SQL for `signal`, or None if the signal isn't
    supported by rev_tracker. Kept separate from execution so we can record
    the intended SQL in evidence_refs even when the query raises."""
    signal_expr = _CHECK_2_SIGNAL_EXPRESSIONS.get(signal)
    if signal_expr is None:
        return None
    return (
        f"SELECT {_REV_TRACKER_TS_COL} AS d, {signal_expr} AS value "
        f"FROM `{_REV_TRACKER_TABLE}` "
        f"WHERE {_REV_TRACKER_ENTITY_COL} = @entity "
        f"AND {_REV_TRACKER_TS_COL} BETWEEN "
        f"DATE_SUB(DATE(@onset_ts), INTERVAL {BASELINE_DAYS} DAY) "
        f"AND DATE_ADD(DATE(@onset_ts), INTERVAL {POST_DAYS} DAY) "
        f"GROUP BY {_REV_TRACKER_TS_COL} "
        f"ORDER BY {_REV_TRACKER_TS_COL}"
    )


def _run_check_2(bq: Any, entity: str, onset_ts: datetime, sql: str) -> dict:
    """Execute a pre-built check-2 SQL and evaluate the persistence result."""
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("entity", "STRING", entity),
            bigquery.ScalarQueryParameter("onset_ts", "TIMESTAMP", onset_ts),
        ]
    )
    rows = list(bq.query(sql, job_config=job_config).result())

    onset_day = onset_ts.date()
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


def _classify_vote(status: str) -> str | None:
    """Map a check status to a SUPPORT/UNDERMINE vote (or None if non-voting).

    Check-1's PASS/FAIL and check-2's SUPPORT/UNDERMINE feed the same tally so
    the "≥2 UNDERMINE and 0 SUPPORT → KILL" rule can be evaluated generally.

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
    plus the standing 'checks 3–7 not yet implemented' note."""
    caveats: list[str] = []
    for name, result in implemented_named:
        if result["status"] in ("NEUTRAL", "UNDERMINE"):
            detail = result.get("detail", "")
            caveats.append(f"{name} {result['status']}: {detail}")
    caveats.append(_NOT_IMPLEMENTED_CAVEAT)
    return caveats


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

    # Build check-2 SQL up front so the intended query is preserved in
    # evidence_refs even if execution raises (fail-closed logging).
    check_2_sql = _check_2_sql_for(event["signal"])
    if check_2_sql is None:
        check_2_result = {
            "status": "NEUTRAL",
            "detail": f"signal {event['signal']!r} not supported by check 2",
        }
    else:
        try:
            check_2_result = _run_check_2(
                bq, event["entity"], onset_ts, check_2_sql,
            )
        except Exception as e:
            check_2_result = {
                "status": "NEUTRAL",
                "detail": f"check 2 errored: {type(e).__name__}: {e}",
                "errored": True,
            }

    check_results: dict = {
        "check_1": check_1_result,
        "check_2": check_2_result,
    }
    for name in _UNIMPLEMENTED_CHECKS:
        check_results[name] = {"status": "NOT_IMPLEMENTED"}

    verdict = _decide_verdict(check_1_result, [check_2_result])

    if verdict == "PASS_WITH_CAVEATS":
        check_results["caveats"] = _build_caveats(
            [("check_1", check_1_result), ("check_2", check_2_result)]
        )

    evidence_refs = {"check_1_sql": check_1_sql}
    if check_2_sql is not None:
        evidence_refs["check_2_sql"] = check_2_sql

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
