"""Event emission for the detector portfolio.

emit_event(payload) validates a detector-fire payload and appends one row to
the `marble-light.operational_intelligence.events` BigQuery table.

Validation is intentionally strict: enum values are checked here (contract §1
says the enum gate lives in Python, not in DDL), types are checked, and NO
coercion happens. Any malformed payload raises ValueError with a specific
violation BEFORE any BigQuery call is made.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

_BQ_PROJECT = "marble-light"
_EVENTS_TABLE = "marble-light.operational_intelligence.events"

_ALLOWED_MODEL_IDS = {"01-tripwire", "02-funnel", "03-cusum", "05-headline"}
_ALLOWED_SIGNALS = {"win_rate", "avg_win_price", "aapv", "rpm"}
_ALLOWED_TIERS = {"top20", "mid"}

_REQUIRED_STR_FIELDS = ("model_id", "signal", "entity", "direction")


def _validate(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError(
            f"payload must be a dict, got {type(payload).__name__}"
        )

    for field in _REQUIRED_STR_FIELDS:
        if field not in payload:
            raise ValueError(f"missing required field: {field!r}")
        if not isinstance(payload[field], str):
            raise ValueError(
                f"field {field!r} must be str, got "
                f"{type(payload[field]).__name__}"
            )

    if "onset_ts" not in payload:
        raise ValueError("missing required field: 'onset_ts'")
    if not isinstance(payload["onset_ts"], datetime):
        raise ValueError(
            f"field 'onset_ts' must be a datetime, got "
            f"{type(payload['onset_ts']).__name__}"
        )
    if payload["onset_ts"].tzinfo is None:
        raise ValueError("field 'onset_ts' must be timezone-aware")

    if "corroborated" not in payload:
        raise ValueError("missing required field: 'corroborated'")
    # bool is a subclass of int; use isinstance(..., bool) explicitly, not int.
    if not isinstance(payload["corroborated"], bool):
        raise ValueError(
            f"field 'corroborated' must be bool, got "
            f"{type(payload['corroborated']).__name__}"
        )

    if payload["model_id"] not in _ALLOWED_MODEL_IDS:
        raise ValueError(
            f"model_id must be one of {sorted(_ALLOWED_MODEL_IDS)}, "
            f"got {payload['model_id']!r}"
        )
    if payload["signal"] not in _ALLOWED_SIGNALS:
        raise ValueError(
            f"signal must be one of {sorted(_ALLOWED_SIGNALS)}, "
            f"got {payload['signal']!r}"
        )
    if payload["direction"] != "down":
        raise ValueError(
            f"direction must be 'down', got {payload['direction']!r}"
        )

    tier = payload.get("tier")
    if tier is not None and tier not in _ALLOWED_TIERS:
        raise ValueError(
            f"tier must be one of {sorted(_ALLOWED_TIERS)} or None, "
            f"got {tier!r}"
        )

    magnitude = payload.get("magnitude")
    if magnitude is not None:
        # reject bool explicitly (bool is int subclass) — never coerce.
        if isinstance(magnitude, bool) or not isinstance(magnitude, (int, float)):
            raise ValueError(
                f"magnitude must be numeric or None, got "
                f"{type(magnitude).__name__}"
            )

    detector_params = payload.get("detector_params")
    if detector_params is not None and not isinstance(detector_params, dict):
        raise ValueError(
            f"detector_params must be a dict or None, got "
            f"{type(detector_params).__name__}"
        )


def _to_iso_utc(dt: datetime) -> str:
    """Serialize an (already-validated) tz-aware datetime to RFC 3339 UTC."""
    return dt.astimezone(timezone.utc).isoformat()


def _default_client():
    # Deferred import so tests can run without ADC configured.
    from google.cloud import bigquery
    return bigquery.Client(project=_BQ_PROJECT)


def emit_event(payload: dict, *, client: Any = None) -> str:
    """Validate `payload`, then append one row to the events table.

    Returns the event_id (a fresh uuid4 if the caller did not supply one).
    Raises ValueError on any validation failure BEFORE contacting BigQuery.
    Raises RuntimeError if BigQuery reports insert errors.

    `client` is an injection seam for tests; production callers omit it.
    """
    _validate(payload)

    event_id = payload.get("event_id") or str(uuid.uuid4())
    emitted_at = datetime.now(timezone.utc)

    row = {
        "event_id": event_id,
        "model_id": payload["model_id"],
        "signal": payload["signal"],
        "entity": payload["entity"],
        "tier": payload.get("tier"),
        "onset_ts": _to_iso_utc(payload["onset_ts"]),
        "direction": payload["direction"],
        "magnitude": payload.get("magnitude"),
        "detector_params": (
            json.dumps(payload["detector_params"])
            if payload.get("detector_params") is not None
            else None
        ),
        "corroborated": payload["corroborated"],
        "emitted_at": emitted_at.isoformat(),
    }

    bq = client if client is not None else _default_client()
    errors = bq.insert_rows_json(_EVENTS_TABLE, [row])
    if errors:
        raise RuntimeError(f"BigQuery insert failed: {errors}")
    return event_id
