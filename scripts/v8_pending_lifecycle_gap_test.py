#!/usr/bin/env python3
"""Research-only contract tests for V8 pending expiry across M5 candle gaps.

These tests deliberately do not import, patch, or modify the live engine. They
pin the intended research policy and make the difference between logical
deadline and observed candle timestamp explicit.
"""
from datetime import datetime, timedelta, timezone


def decision(confirmation, candle_time, timeout_minutes, technical_invalid):
    """Return the terminal decision at the first candle the simulator processes."""
    deadline = confirmation + timedelta(minutes=timeout_minutes)
    if candle_time >= deadline:
        return "EXPIRED", deadline
    if technical_invalid:
        return "INVALIDATED", deadline
    return "PENDING", deadline


def dt(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def run():
    cases = [
        # Before deadline, technical invalidation must win.
        ("invalidated_before_deadline", "2026-07-07T20:30:00+00:00",
         "2026-07-07T21:25:00+00:00", 60, True, "INVALIDATED"),
        # At deadline, expiry takes priority even if the same candle would
        # otherwise trigger technical invalidation.
        ("expiry_at_deadline_has_priority", "2026-07-07T20:30:00+00:00",
         "2026-07-07T21:30:00+00:00", 60, True, "EXPIRED"),
        # After a market/data gap, expiry is still the logical outcome; the
        # observed terminal candle is later than the deadline.
        ("expiry_after_candle_gap", "2026-07-07T20:30:00+00:00",
         "2026-07-07T22:00:00+00:00", 60, True, "EXPIRED"),
        # No invalidation and still before the deadline remains pending.
        ("still_pending_before_deadline", "2026-07-07T20:30:00+00:00",
         "2026-07-07T21:20:00+00:00", 60, False, "PENDING"),
        # 90-minute timeout remains pending at minute 65 despite the same gap.
        ("longer_timeout_survives_gap_until_deadline", "2026-07-07T20:30:00+00:00",
         "2026-07-07T21:35:00+00:00", 90, False, "PENDING"),
    ]
    for name, start, current, timeout, invalid, expected in cases:
        result, deadline = decision(dt(start), dt(current), timeout, invalid)
        assert result == expected, f"{name}: expected {expected}, got {result}"
        if name == "expiry_after_candle_gap":
            observed = dt(current)
            assert observed > deadline
            assert (observed - deadline).total_seconds() == 30 * 60
        print(f"PASS {name}: {result}")

    print(f"PASS all {len(cases)} pending lifecycle contract tests")
    print("SCOPE=research-only; live engine/dashboard/cron unchanged; auto-trade disabled")


if __name__ == "__main__":
    run()
