"""One-time backfill: emit 76 Model 05 headline events from a CSV.

Not package code; not covered by tests. Committed for the record after the
one successful run. Reads scripts/headline_onsets_backfill.csv and appends
one row per CSV row to `marble-light.operational_intelligence.events` via
detector.emit_event.

Usage:
    python scripts/backfill_model05.py --dry-run   # validate only
    python scripts/backfill_model05.py             # write 76 rows to BQ

CSV columns (all required):
    orgName, signal, onset_date, fire_date, cusum_peak

Mapping applied to each row:
    model_id         = "05-headline"
    signal           = <signal>        (validated against emit_event enum)
    entity           = <orgName>
    tier             = "top20"
    direction        = "down"
    corroborated     = False
    onset_ts         = midnight UTC of <onset_date>  (tz-aware)
    magnitude        = float(<cusum_peak>)
    detector_params  = {"K": 0.5, "H": 8.0, "warmup": 14,
                        "fire_date": <fire_date as string>}
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from detector import emit_event
# Private validator: needed so we can validate ALL 76 payloads BEFORE the
# first emit_event() call. That way a bad row means zero writes.
from detector.events import _validate as _validate_payload

_CSV_PATH = Path(__file__).parent / "headline_onsets_backfill.csv"
_EXPECTED_ROWS = 76
_REQUIRED_COLS = ("orgName", "signal", "onset_date", "fire_date", "cusum_peak")


def _load_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in _REQUIRED_COLS if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(
                f"CSV missing required columns: {missing}\n"
                f"  found: {reader.fieldnames}"
            )
        return list(reader)


def _row_to_payload(row: dict) -> dict:
    onset_raw = row["onset_date"].strip()
    d = date.fromisoformat(onset_raw[:10])  # accepts "YYYY-MM-DD" (+ optional trailing time)
    onset_ts = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return {
        "model_id": "05-headline",
        "signal": row["signal"].strip(),
        "entity": row["orgName"].strip(),
        "tier": "top20",
        "direction": "down",
        "corroborated": False,
        "onset_ts": onset_ts,
        "magnitude": float(row["cusum_peak"]),
        "detector_params": {
            "K": 0.5,
            "H": 8.0,
            "warmup": 14,
            "fire_date": row["fire_date"].strip(),
        },
    }


def _build_and_validate_all(rows: list[dict]) -> list[dict]:
    payloads: list[dict] = []
    for i, row in enumerate(rows, start=1):
        try:
            payload = _row_to_payload(row)
            _validate_payload(payload)
        except Exception as e:
            raise SystemExit(
                f"row {i} invalid ({type(e).__name__}): {e}\n  row = {row}"
            )
        payloads.append(payload)
    return payloads


def _pprint(label: str, obj) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(obj, default=str, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Model 05 headline backfill.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate all payloads and print the first; skip BigQuery writes.",
    )
    args = parser.parse_args()

    if not _CSV_PATH.exists():
        raise SystemExit(f"CSV not found: {_CSV_PATH}")

    rows = _load_rows(_CSV_PATH)
    print(f"Loaded {len(rows)} rows from {_CSV_PATH}")

    if len(rows) != _EXPECTED_ROWS:
        raise SystemExit(
            f"expected exactly {_EXPECTED_ROWS} rows, got {len(rows)}"
        )

    payloads = _build_and_validate_all(rows)
    print(f"Built and validated {len(payloads)} payloads.")

    _pprint("first payload", payloads[0])

    if args.dry_run:
        print("\nDry run complete — no BigQuery writes performed.")
        return 0

    print("\nEmitting to BigQuery...")
    event_ids = [emit_event(p) for p in payloads]
    print(
        f"\nEmitted {len(event_ids)} events.\n"
        f"  first event_id = {event_ids[0]}\n"
        f"  last  event_id = {event_ids[-1]}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
