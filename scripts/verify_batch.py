"""Batch verify: run verify_event across every unverified event.

Not package code; not tested. Discovers events in
`marble-light.operational_intelligence.events` that don't yet have a row in
the verdicts table, runs verify_event on each sequentially, and prints a
per-event outcome plus a final tally.

Usage:
    python scripts/verify_batch.py --dry-run   # list unverified events
    python scripts/verify_batch.py             # verify them all

The NOT EXISTS filter honours SPEC_verifier_check2_persistence.md's
"no re-verification of already-verdicted events" out-of-scope rule.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from detector import verify_event
from detector.verifier import _default_client

_UNVERIFIED_SQL = """
SELECT e.event_id, e.model_id, e.entity, e.signal, e.onset_ts
FROM `marble-light.operational_intelligence.events` e
WHERE NOT EXISTS (
    SELECT 1
    FROM `marble-light.operational_intelligence.verdicts` v
    WHERE v.event_id = e.event_id
)
ORDER BY e.onset_ts
"""


def _find_unverified(bq) -> list[dict]:
    rows = list(bq.query(_UNVERIFIED_SQL).result())
    return [
        {
            "event_id": r["event_id"],
            "model_id": r["model_id"],
            "entity": r["entity"],
            "signal": r["signal"],
            "onset_ts": r["onset_ts"],
        }
        for r in rows
    ]


def _print_preview(events: list[dict], head: int = 5) -> None:
    for e in events[:head]:
        print(
            f"  {e['event_id']}  {e['model_id']:<12} "
            f"{e['entity']:<25} {e['signal']:<5} {e['onset_ts']}"
        )
    if len(events) > head:
        print(f"  ... and {len(events) - head} more")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch verify all events without a verdict."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List unverified events; skip verify_event and BQ verdicts writes.",
    )
    args = parser.parse_args()

    bq = _default_client()
    unverified = _find_unverified(bq)
    print(f"Found {len(unverified)} unverified events.")

    if not unverified:
        print("Nothing to do.")
        return 0

    _print_preview(unverified)

    if args.dry_run:
        print("\nDry run — no verifications performed.")
        return 0

    print("\nVerifying...")
    verdicts: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []

    total = len(unverified)
    for i, e in enumerate(unverified, start=1):
        eid = e["event_id"]
        try:
            result = verify_event(eid, client=bq)
            verdicts.append((eid, result["verdict"]))
            print(
                f"  [{i:>3}/{total}] {eid}  {e['entity']:<25} -> {result['verdict']}"
            )
        except Exception as ex:  # pragma: no cover — surface all failures
            errors.append((eid, f"{type(ex).__name__}: {ex}"))
            print(
                f"  [{i:>3}/{total}] {eid}  {e['entity']:<25} -> ERROR: "
                f"{type(ex).__name__}: {ex}"
            )

    print(f"\nDone. Processed {len(verdicts)} events, {len(errors)} errors.")
    counter = Counter(v for _, v in verdicts)
    for verdict, count in sorted(counter.items()):
        print(f"  {verdict}: {count}")

    if errors:
        print("\nErrors:")
        for eid, msg in errors:
            print(f"  {eid} -> {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
