"""One-time re-verification pass over the 76 Model 05 headline events
under config_version verifier-0.0.4-check3 (adds check 3 classification
alongside checks 1, 2, 6).

Not package code; not tested. Driver only — no verification logic here.

Idempotency: running this twice appends a second set of 0.0.4 rows.
That is not corruption — consumers read the latest verdict per event_id
— but it does muddy the audit trail, so do not re-run casually.

Usage:
    python scripts/reverify_004.py --dry-run   # fetch + count, no writes
    python scripts/reverify_004.py             # re-verify all 76 events
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from detector import verify_event
from detector.verifier import _default_client


_EXPECTED_EVENTS = 76
_CONFIG_VERSION = "verifier-0.0.4-check3"
_SUMMARY_PATH = Path(__file__).resolve().parent.parent / "reverify_004_summary.md"

# Deterministic ordering so reruns are comparable line-by-line.
_EVENTS_SQL = """
SELECT event_id, entity, signal, DATE(onset_ts) AS onset
FROM `marble-light.operational_intelligence.events`
WHERE model_id = '05-headline'
ORDER BY onset_ts, event_id
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


def _fmt_delta(v) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.2f}"


def _build_summary(
    records: list[dict], errors: list[tuple[str, str]]
) -> str:
    """Build the markdown summary body (also mirrors console output)."""
    lines: list[str] = []
    total = len(records) + len(errors)
    lines.append(f"# Re-verification summary — {_CONFIG_VERSION}")
    lines.append("")
    lines.append(f"- Processed: {total}")
    lines.append(f"- Succeeded: {len(records)}")
    lines.append(f"- Failed:    {len(errors)}")
    lines.append("")

    # Verdict distribution
    lines.append("## Verdict distribution")
    verdict_counts = Counter(r["verdict"] for r in records)
    for v, n in sorted(verdict_counts.items()):
        lines.append(f"- {v}: {n}")
    unexpected = {k: n for k, n in verdict_counts.items() if k != "PASS_WITH_CAVEATS"}
    if unexpected:
        lines.append("")
        lines.append(f"**RED FLAG**: expected all PASS_WITH_CAVEATS; got {unexpected}.")
    lines.append("")

    # Check 6
    lines.append("## Check 6 (seasonal twin) vote distribution")
    lines.append("Prior baseline (0.0.3-check6): SUPPORT 21 / UNDERMINE 47 / NEUTRAL 8")
    c6_counts = Counter(r["check_6"] for r in records)
    for status in ("SUPPORT", "UNDERMINE", "NEUTRAL"):
        lines.append(f"- {status}: {c6_counts.get(status, 0)}")
    expected_c6 = {"SUPPORT": 21, "UNDERMINE": 47, "NEUTRAL": 8}
    drift = {
        k: (c6_counts.get(k, 0), expected_c6[k])
        for k in expected_c6
        if c6_counts.get(k, 0) != expected_c6[k]
    }
    if drift:
        lines.append("")
        lines.append("**RED FLAG**: check 6 drifted from baseline (was not supposed to change):")
        for k, (got, want) in drift.items():
            lines.append(f"  - {k}: got {got}, expected {want}")
    lines.append("")

    # Check 2
    lines.append("## Check 2 (persistence) vote distribution")
    c2_counts = Counter(r["check_2"] for r in records)
    for status, n in sorted(c2_counts.items()):
        lines.append(f"- {status}: {n}")
    lines.append("")

    # Check 3
    lines.append("## Check 3 (cross-signal classification) distribution")
    c3_counts = Counter(r["check_3_class"] for r in records)
    for cls, n in sorted(c3_counts.items()):
        lines.append(f"- {cls}: {n}")
    lines.append("")

    # Queue preview
    survivors = [r for r in records if r["check_6"] != "UNDERMINE"]

    def _sort_key(r):
        d = r["check_3_delta"]
        # None sorts LAST (INSUFFICIENT_DATA / errored); real deltas sort ascending
        # (most negative first).
        return (d is None, d if d is not None else 0.0)

    survivors.sort(key=_sort_key)
    lines.append(f"## Queue preview — non-seasonal survivors (check 6 != UNDERMINE) — {len(survivors)}")
    lines.append("")
    lines.append(
        "| entity | signal | onset | check-3 classification "
        "| est_daily_revenue_delta | check-2 vote |"
    )
    lines.append("|---|---|---|---|---:|---|")
    for r in survivors:
        lines.append(
            f"| {r['entity']} | {r['signal']} | {r['onset']} "
            f"| {r['check_3_class']} | {_fmt_delta(r['check_3_delta'])} "
            f"| {r['check_2']} |"
        )
    lines.append("")

    # INSUFFICIENT_DATA list
    insuff = [r for r in records if r["check_3_class"] == "INSUFFICIENT_DATA"]
    lines.append(f"## INSUFFICIENT_DATA classifications — {len(insuff)}")
    for r in insuff:
        lines.append(f"- {r['event_id']}  {r['entity']}  {r['signal']}  {r['onset']}")
    lines.append("")

    # Errors
    if errors:
        lines.append(f"## Errors ({len(errors)})")
        for eid, msg in errors:
            lines.append(f"- {eid}: {msg}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Re-verify all 76 Model 05 events under {_CONFIG_VERSION}."
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

    print(f"\nRe-verifying under {_CONFIG_VERSION}...")
    records: list[dict] = []
    errors: list[tuple[str, str]] = []

    total = len(events)
    for i, e in enumerate(events, start=1):
        eid = e["event_id"]
        try:
            result = verify_event(eid, client=bq)
            c3 = result["check_results"]["check_3"]
            # check 3 status is IMPLEMENTED on success (with classification)
            # or NEUTRAL on errored. Errored → no classification key.
            c3_class = c3.get("classification", "ERRORED")
            c3_delta = c3.get("est_daily_revenue_delta")
            record = {
                "event_id": eid,
                "entity": e["entity"],
                "signal": e["signal"],
                "onset": e["onset"],
                "verdict": result["verdict"],
                "check_2": result["check_results"]["check_2"]["status"],
                "check_6": result["check_results"]["check_6"]["status"],
                "check_3_class": c3_class,
                "check_3_delta": c3_delta,
            }
            records.append(record)
            print(
                f"  [{i:>3}/{total}] {e['entity']:<25} {e['signal']:<5} "
                f"{e['onset']} -> {result['verdict']} "
                f"(c3={c3_class})"
            )
        except Exception as ex:  # pragma: no cover — surface all failures
            errors.append((eid, f"{type(ex).__name__}: {ex}"))
            print(
                f"  [{i:>3}/{total}] {e['entity']:<25} {e['signal']:<5} "
                f"{e['onset']} -> ERROR: {type(ex).__name__}: {ex}"
            )

    summary = _build_summary(records, errors)
    print("\n" + summary)
    _SUMMARY_PATH.write_text(summary, encoding="utf-8")
    print(f"\nSummary written to {_SUMMARY_PATH}")

    if errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
