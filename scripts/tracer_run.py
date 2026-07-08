"""Live tracer bullet — one fake event through emit_event() and verify_event().

Writes exactly one row to `marble-light.operational_intelligence.events`
and exactly one row to `marble-light.operational_intelligence.verdicts`.

Usage:
    python scripts/tracer_run.py

Auth: application default credentials. Run `gcloud auth application-default
login` first if the BQ calls fail with a credential error.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from detector import emit_event, verify_event


def _payload() -> dict:
    return {
        "model_id": "05-headline",
        "signal": "aapv",
        "entity": "Publift",
        "onset_ts": datetime(2026, 5, 23, tzinfo=timezone.utc),
        "direction": "down",
        "magnitude": 8.0,
        "detector_params": {"K": 0.5, "H": 8.0, "warmup": 14},
        "corroborated": False,
    }


def _pprint(label: str, obj) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(obj, default=str, indent=2, ensure_ascii=False))


def main() -> int:
    payload = _payload()
    _pprint("payload", payload)

    event_id = emit_event(payload)
    print(f"\nemit_event -> event_id = {event_id}")

    result = verify_event(event_id)
    _pprint("verify_event result", result)

    print(
        "\nDone. Check BigQuery:\n"
        f"  events   — event_id = {event_id}\n"
        f"  verdicts — event_id = {event_id}, verdict = {result['verdict']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
