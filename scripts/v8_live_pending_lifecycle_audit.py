#!/usr/bin/env python3
"""Research-only audit of the actual V8 pending lifecycle implementation.

This script imports the locked live module read-only and inspects its actual
run_v8 source. It does not patch, execute orders, write live state, or change
production behavior. The deterministic scenarios model the exact branch order
found in the engine and expose the known distinction between deadline and
first candle processed after a data gap.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import fvg_v8_live_dry_run as live


def outcome_like_engine(age_minutes: float, technical_invalid: bool) -> str:
    """Mirror the actual run_v8 pending branch order, verified below."""
    # Actual source checks technical_invalid before checking pending age.
    if technical_invalid:
        return "INVALIDATED"
    if age_minutes >= live.PENDING_ENTRY_MAX_MINUTES:
        return "EXPIRED"
    return "PENDING"


def main() -> None:
    source = inspect.getsource(live.run_v8)
    assert live.PENDING_ENTRY_MAX_MINUTES == 60, (
        f"Expected locked 60m pending timeout, got {live.PENDING_ENTRY_MAX_MINUTES}"
    )
    invalid_idx = source.find("if technical_invalid:")
    age_idx = source.find("if pending_age_minutes >= PENDING_ENTRY_MAX_MINUTES:")
    assert invalid_idx >= 0 and age_idx >= 0, "Pending lifecycle branches not found"
    assert invalid_idx < age_idx, (
        "Live lifecycle order changed; review manually instead of assuming expected behavior"
    )
    print("PASS actual_live_module_imported: run_v8")
    print("PASS locked_pending_timeout: 60 minutes")
    print("PASS source_order_verified: technical invalidation precedes timeout")

    scenarios = [
        ("before_deadline_invalidated", 55, True, "INVALIDATED"),
        ("at_deadline_no_invalidation", 60, False, "EXPIRED"),
        ("after_deadline_no_invalidation", 90, False, "EXPIRED"),
        # Critical case: first available candle is after deadline and closes
        # beyond FVG boundary. Actual engine chooses invalidation due to order.
        ("after_deadline_gap_with_invalidation", 90, True, "INVALIDATED"),
        ("before_deadline_valid", 55, False, "PENDING"),
    ]
    for name, age, invalid, expected in scenarios:
        actual = outcome_like_engine(age, invalid)
        assert actual == expected, f"{name}: expected {expected}, got {actual}"
        print(f"PASS {name}: {actual}")

    print("FINDING=after-deadline candle gap + technical invalidation yields INVALIDATED, not EXPIRED")
    print("LIMITATION=engine processes closed M5 candles; it has no independent wall-clock expiry callback")
    print("SCOPE=read-only research audit; no live engine/dashboard/cron changes; auto-trade disabled")


if __name__ == "__main__":
    main()
