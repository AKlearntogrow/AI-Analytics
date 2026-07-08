"""Tests for detector.events.emit_event — validation-only, BigQuery mocked.

Coverage per SPEC Step 1:
  1. Valid payload passes and returns an event_id.
  2. Each missing required field raises ValueError.
  3. Each bad enum value raises ValueError.
  4. Wrong types are rejected (never coerced).
  5. Model 05 payload with corroborated=false passes.

All BigQuery I/O is stubbed via a fake client — no ADC, no network.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from detector import emit_event
from detector import events as events_mod


class _FakeBQClient:
    """Captures insert_rows_json calls; returns [] (no errors) by default."""

    def __init__(self, errors=None):
        self._errors = errors or []
        self.calls = []

    def insert_rows_json(self, table, rows):
        self.calls.append((table, rows))
        return self._errors


def _valid_payload(**overrides):
    payload = {
        "model_id": "05-headline",
        "signal": "aapv",
        "entity": "Publift",
        "tier": None,
        "onset_ts": datetime(2026, 5, 23, tzinfo=timezone.utc),
        "direction": "down",
        "magnitude": 8.0,
        "detector_params": {"K": 0.5, "H": 8.0, "warmup": 14},
        "corroborated": False,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------

def test_valid_payload_writes_one_row_and_returns_event_id():
    client = _FakeBQClient()
    event_id = emit_event(_valid_payload(), client=client)

    assert isinstance(event_id, str) and len(event_id) > 0
    assert len(client.calls) == 1
    table, rows = client.calls[0]
    assert table == "marble-light.operational_intelligence.events"
    assert len(rows) == 1
    row = rows[0]

    # Auto-filled fields
    assert row["event_id"] == event_id
    assert row["emitted_at"].endswith("+00:00")

    # Pass-through fields
    assert row["model_id"] == "05-headline"
    assert row["signal"] == "aapv"
    assert row["entity"] == "Publift"
    assert row["tier"] is None
    assert row["direction"] == "down"
    assert row["magnitude"] == 8.0
    assert row["corroborated"] is False

    # onset_ts serialized as RFC 3339 UTC
    assert row["onset_ts"] == "2026-05-23T00:00:00+00:00"

    # detector_params serialized as JSON string
    assert json.loads(row["detector_params"]) == {"K": 0.5, "H": 8.0, "warmup": 14}


def test_supplied_event_id_is_preserved():
    client = _FakeBQClient()
    supplied = "fixed-id-abc"
    returned = emit_event(_valid_payload(event_id=supplied), client=client)
    assert returned == supplied
    assert client.calls[0][1][0]["event_id"] == supplied


def test_model_05_with_corroborated_false_passes():
    client = _FakeBQClient()
    payload = _valid_payload(model_id="05-headline", corroborated=False)
    emit_event(payload, client=client)
    row = client.calls[0][1][0]
    assert row["model_id"] == "05-headline"
    assert row["corroborated"] is False


def test_bq_insert_errors_raise_runtime_error():
    client = _FakeBQClient(errors=[{"index": 0, "errors": [{"reason": "invalid"}]}])
    with pytest.raises(RuntimeError, match="BigQuery insert failed"):
        emit_event(_valid_payload(), client=client)


def test_missing_optional_fields_default_to_none():
    client = _FakeBQClient()
    payload = _valid_payload()
    payload.pop("tier")
    payload.pop("magnitude")
    payload.pop("detector_params")
    emit_event(payload, client=client)
    row = client.calls[0][1][0]
    assert row["tier"] is None
    assert row["magnitude"] is None
    assert row["detector_params"] is None


# ---------------------------------------------------------------------------
# 2. Missing required fields
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "missing",
    ["model_id", "signal", "entity", "direction", "onset_ts", "corroborated"],
)
def test_missing_required_field_raises(missing):
    payload = _valid_payload()
    payload.pop(missing)
    with pytest.raises(ValueError, match=f"missing required field: '{missing}'"):
        emit_event(payload, client=_FakeBQClient())


# ---------------------------------------------------------------------------
# 3. Bad enum values
# ---------------------------------------------------------------------------

def test_bad_model_id_raises():
    with pytest.raises(ValueError, match="model_id must be one of"):
        emit_event(_valid_payload(model_id="04-svar"), client=_FakeBQClient())


def test_bad_signal_raises():
    with pytest.raises(ValueError, match="signal must be one of"):
        emit_event(_valid_payload(signal="ctr"), client=_FakeBQClient())


def test_direction_up_raises():
    with pytest.raises(ValueError, match="direction must be 'down'"):
        emit_event(_valid_payload(direction="up"), client=_FakeBQClient())


def test_bad_tier_raises():
    with pytest.raises(ValueError, match="tier must be one of"):
        emit_event(_valid_payload(tier="platinum"), client=_FakeBQClient())


def test_tier_none_is_allowed():
    emit_event(_valid_payload(tier=None), client=_FakeBQClient())


# ---------------------------------------------------------------------------
# 4. Wrong types (no coercion)
# ---------------------------------------------------------------------------

def test_onset_ts_as_string_raises():
    with pytest.raises(ValueError, match="'onset_ts' must be a datetime"):
        emit_event(
            _valid_payload(onset_ts="2026-05-23T00:00:00Z"),
            client=_FakeBQClient(),
        )


def test_onset_ts_naive_datetime_raises():
    with pytest.raises(ValueError, match="timezone-aware"):
        emit_event(
            _valid_payload(onset_ts=datetime(2026, 5, 23)),
            client=_FakeBQClient(),
        )


def test_corroborated_int_raises():
    # bool is int subclass — the validator must still reject a raw int.
    with pytest.raises(ValueError, match="'corroborated' must be bool"):
        emit_event(_valid_payload(corroborated=1), client=_FakeBQClient())


def test_entity_non_string_raises():
    with pytest.raises(ValueError, match="'entity' must be str"):
        emit_event(_valid_payload(entity=123), client=_FakeBQClient())


def test_magnitude_bool_rejected():
    # bool is int subclass — must NOT be silently accepted as numeric.
    with pytest.raises(ValueError, match="magnitude must be numeric"):
        emit_event(_valid_payload(magnitude=True), client=_FakeBQClient())


def test_magnitude_int_ok():
    client = _FakeBQClient()
    emit_event(_valid_payload(magnitude=5), client=client)
    assert client.calls[0][1][0]["magnitude"] == 5


def test_magnitude_none_ok():
    client = _FakeBQClient()
    emit_event(_valid_payload(magnitude=None), client=client)
    assert client.calls[0][1][0]["magnitude"] is None


def test_detector_params_non_dict_raises():
    with pytest.raises(ValueError, match="detector_params must be a dict"):
        emit_event(
            _valid_payload(detector_params="K=0.5"),
            client=_FakeBQClient(),
        )


def test_non_dict_payload_raises():
    with pytest.raises(ValueError, match="payload must be a dict"):
        emit_event("not-a-dict", client=_FakeBQClient())


# ---------------------------------------------------------------------------
# 5. Validation runs BEFORE any BQ call (crash early)
# ---------------------------------------------------------------------------

def test_validation_runs_before_bigquery_call():
    """A bad payload must never reach insert_rows_json."""
    client = _FakeBQClient()
    with pytest.raises(ValueError):
        emit_event(_valid_payload(model_id="bogus"), client=client)
    assert client.calls == [], "insert_rows_json was called despite bad payload"


def test_default_client_not_constructed_when_client_injected(monkeypatch):
    """If a client is passed in, _default_client must not be called."""
    def _boom():
        raise AssertionError("_default_client should not be called")

    monkeypatch.setattr(events_mod, "_default_client", _boom)
    emit_event(_valid_payload(), client=_FakeBQClient())
