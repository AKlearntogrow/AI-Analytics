"""One-time re-verification pass over the 76 Model 05 headline events.

Not package code; not tested. Writes one new verdict row per event under
config_version "verifier-0.0.3-check6". Verdicts are append-only — the
prior "verifier-0.0.2-check2" rows stay in the table as an audit trail.

Usage:
    python scripts/reverify_all.py --dry-run   # fetch + count, no writes
    python scripts/reverify_all.py             # re-verify all 76 events

Expects exactly 76 events; crashes otherwise.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from detector import verify_event
from detector.verifier import _default_client


_EXPECTED_EVENTS = 76

_EVENTS_SQL = """
SELECT event_id, entity, signal, DATE(onset_ts) AS onset
FROM `marble-light.operational_intelligence.events`
WHERE model_id = '05-headline'
ORDER BY onset_ts
"""


def _fetch_events(bq) -> list[dict]:
    rows = list(bq.query(_EVENTS_SQL).result())
    return [
        {
            "event_id": r["event_id"],
            "entity": r["entity"],
            "signal": r["signal"],
            "onset": r["onset"],
        }
        for r in rows
    ]


def _print_preview(events: list[dict], head: int = 5) -> None:
    for e in events[:head]:
        print(
            f"  {e['event_id']}  {e['entity']:<25} "
            f"{e['signal']:<5} {e['onset']}"
        )
    if len(events) > head:
        print(f"  ... and {len(events) - head} more")


def _print_summary(records: list[dict]) -> None:
    print(f"\nDone. {len(records)} verdict rows written under verifier-0.0.3-check6.\n")

    print("Verdict counts:")
    for verdict, count in sorted(Counter(r["verdict"] for r in records).items()):
        print(f"  {verdict:<20} {count}")

    print("\nCheck 2 (persistence):")
    for status, count in sorted(Counter(r["check_2"] for r in records).items()):
        print(f"  {status:<12} {count}")

    print("\nCheck 6 (seasonal twin):")
    for status, count in sorted(Counter(r["check_6"] for r in records).items()):
        print(f"  {status:<12} {count}")

    undermined = [r for r in records if r["check_6"] == "UNDERMINE"]
    print(f"\nCheck 6 UNDERMINE events (seasonal cluster) — {len(undermined)}:")
    for r in undermined:
        print(f"  {r['entity']:<25} {r['signal']:<5} {r['onset']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-verify all 76 Model 05 events under 0.0.3-check6."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch + count events; skip verify_event and BQ verdicts writes.",
    )
    args = parser.parse_args()

    bq = _default_client()
    events = _fetch_events(bq)
    print(f"Fetched {len(events)} Model 05 headline events from BigQuery.")

    if len(events) != _EXPECTED_EVENTS:
        raise SystemExit(
            f"expected exactly {_EXPECTED_EVENTS} events, got {len(events)}"
        )

    _print_preview(events)

    if args.dry_run:
        print("\nDry run complete — no BigQuery writes performed.")
        return 0

    print("\nRe-verifying...")
    records: list[dict] = []
    errors: list[tuple[str, str]] = []

    total = len(events)
    for i, e in enumerate(events, start=1):
        eid = e["event_id"]
        try:
            result = verify_event(eid, client=bq)
            record = {
                "event_id": eid,
                "entity": e["entity"],
                "signal": e["signal"],
                "onset": e["onset"],
                "verdict": result["verdict"],
                "check_2": result["check_results"]["check_2"]["status"],
                "check_6": result["check_results"]["check_6"]["status"],
            }
            records.append(record)
            print(
                f"  [{i:>3}/{total}] {e['entity']:<25} {e['signal']:<5} "
                f"{e['onset']} -> {result['verdict']} "
                f"(c2={record['check_2']}, c6={record['check_6']})"
            )
        except Exception as ex:  # pragma: no cover — surface all failures
            errors.append((eid, f"{type(ex).__name__}: {ex}"))
            print(
                f"  [{i:>3}/{total}] {e['entity']:<25} {e['signal']:<5} "
                f"{e['onset']} -> ERROR: {type(ex).__name__}: {ex}"
            )

    _print_summary(records)

    if errors:
        print(f"\nErrors ({len(errors)}):")
        for eid, msg in errors:
            print(f"  {eid} -> {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
