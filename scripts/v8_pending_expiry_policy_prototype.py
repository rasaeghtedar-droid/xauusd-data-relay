#!/usr/bin/env python3
"""Research-only prototype for deterministic V8 pending-expiry policy.

No live module is modified. This models the recommended priority using the
confirmation timestamp as the immutable deadline and treats wall-clock expiry
as authoritative even when the next M5 candle arrives late.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Decision:
    status: str
    deadline: datetime
    observed_at: datetime
    delay_minutes: float
    reason: str


def decide_pending(
    confirmation_time: datetime,
    observed_time: datetime,
    timeout_minutes: int,
    technical_invalid: bool,
    entry_touched_before_deadline: bool = False,
) -> Decision:
    if confirmation_time.tzinfo is None or observed_time.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    if timeout_minutes <= 0:
        raise ValueError("timeout_minutes must be positive")
    deadline = confirmation_time + timedelta(minutes=timeout_minutes)
    delay = max(0.0, (observed_time - deadline).total_seconds() / 60.0)

    # Entry must have been touched strictly before the deadline to activate.
    # At/after deadline, a not-yet-activated setup is expired first, regardless
    # of what the late-arriving candle says about technical invalidation.
    if not entry_touched_before_deadline and observed_time >= deadline:
        return Decision("EXPIRED", deadline, observed_time, delay,
                        "pending deadline reached before entry activation")
    if technical_invalid:
        return Decision("INVALIDATED", deadline, observed_time, delay,
                        "closed M5 candle crossed original FVG boundary before deadline")
    return Decision("PENDING", deadline, observed_time, delay,
                    "deadline not reached and no technical invalidation")


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def main() -> None:
    start = dt("2026-07-07T20:30:00+00:00")
    tests = [
        ("technical_invalidation_before_deadline",
         start + timedelta(minutes=55), True, False, "INVALIDATED"),
        ("exact_deadline_expires",
         start + timedelta(minutes=60), False, False, "EXPIRED"),
        ("late_candle_gap_and_invalidation_expires",
         start + timedelta(minutes=90), True, False, "EXPIRED"),
        ("late_candle_gap_without_invalidation_expires",
         start + timedelta(minutes=90), False, False, "EXPIRED"),
        ("valid_pending_before_deadline",
         start + timedelta(minutes=55), False, False, "PENDING"),
        ("entry_already_activated_is_not_pending_expiry",
         start + timedelta(minutes=90), True, True, "INVALIDATED"),
    ]
    for name, observed, invalid, activated, expected in tests:
        result = decide_pending(start, observed, 60, invalid, activated)
        assert result.status == expected, (
            f"{name}: expected {expected}, got {result.status}"
        )
        print(f"PASS {name}: {result.status}; delay_after_deadline={result.delay_minutes:.1f}m")

    # Explicitly verify deadline is stable and late candle doesn't redefine it.
    gap = decide_pending(start, start + timedelta(minutes=90), 60, True)
    assert gap.deadline == start + timedelta(minutes=60)
    assert gap.delay_minutes == 30.0
    print("PASS immutable_deadline: deadline remains confirmation + 60m")
    print("PASS all 7 research-only pending policy tests")
    print("SCOPE=prototype only; live engine/dashboard/cron unchanged; auto-trade disabled")


if __name__ == "__main__":
    main()
