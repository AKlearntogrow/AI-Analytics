"""Verifier stub for the detector portfolio (v0 tracer bullet — check 1 only).

verify_event(event_id) reads one row from the events table, runs check 1
(data-integrity stub against rev_tracker), and appends one verdict row to the
verdicts table.

HARD CONSTRAINT (SPEC Step 2): the verifier code path is read-only against
source data. The ONLY write is the single append to the verdicts table.
No DML/DDL anywhere else.

Checks 2–7 are not yet implemented; they are recorded as NOT_IMPLEMENTED in
`check_results` and, when check 1 passes, the verdict is PASS_WITH_CAVEATS.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

_BQ_PROJECT = "marble-light"
_EVENTS_TABLE = "marble-light.operational_intelligence.events"
_VERDICTS_TABLE = "marble-light.operational_intelligence.verdicts"
_REV_TRACKER_TABLE = "marble-light.counters.rev_tracker"

_CONFIG_VERSION = "verifier-0.0.1-tracer"
_NOT_IMPLEMENTED_CAVEAT = "checks 2–7 not yet implemented"
_UNIMPLEMENTED_CHECKS = (
    "check_2", "check_3", "check_4", "check_5", "check_6", "check_7",
)

_REV_TRACKER_ENTITY_COL = "orgName"
_REV_TRACKER_TS_COL = "date"


def _default_client():
    from google.cloud import bigquery
    return bigquery.Client(project=_BQ_PROJECT)


def _fetch_event(bq: Any, event_id: str) -> dict:
    from google.cloud import bigquery

    sql = (
        f"SELECT entity, onset_ts FROM `{_EVENTS_TABLE}` "
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
    return {"entity": row["entity"], "onset_ts": row["onset_ts"]}


def _run_check_1(bq: Any, entity: str, onset_ts: datetime) -> tuple[dict, str]:
    """Return (check_1_result, sql_text)."""
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


def verify_event(event_id: str, *, client: Any = None) -> dict:
    """Read one event, run check 1, write one verdict row.

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

    check_1_result, check_1_sql = _run_check_1(bq, event["entity"], onset_ts)

    check_results: dict = {"check_1": check_1_result}
    for name in _UNIMPLEMENTED_CHECKS:
        check_results[name] = {"status": "NOT_IMPLEMENTED"}

    if check_1_result["status"] == "PASS":
        verdict = "PASS_WITH_CAVEATS"
        check_results["caveats"] = [_NOT_IMPLEMENTED_CAVEAT]
    else:
        verdict = "KILL"

    evidence_refs = {"check_1_sql": check_1_sql}
    runtime_seconds = time.monotonic() - started

    if onset_ts.tzinfo is None:
        onset_ts = onset_ts.replace(tzinfo=timezone.utc)

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
