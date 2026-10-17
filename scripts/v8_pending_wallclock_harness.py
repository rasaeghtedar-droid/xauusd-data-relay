#!/usr/bin/env python3
"""Research-only wall-clock pending-expiry harness.

This is an isolated behavioral harness, not wired into the live V8 engine.
It demonstrates expiry being evaluated on each scheduler invocation before
processing any new candle, so a missing candle does not keep a pending setup
actionable. Scheduler delays still bound the observed response time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class Pending:
    confirmed_at: datetime
    timeout_minutes: int = 60
    entry_activated: bool = False
    terminal_status: str | None = None
    terminal_at: datetime | None = None
    terminal_reason: str | None = None

    @property
    def deadline(self) -> datetime:
        return self.confirmed_at + timedelta(minutes=self.timeout_minutes)


def poll_wall_clock(pending: Pending, now: datetime) -> Pending:
    """Call at the beginning of every relay run, before candle processing."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if pending.terminal_status is not None or pending.entry_activated:
        return pending
    if now >= pending.deadline:
        pending.terminal_status = "EXPIRED"
        pending.terminal_at = now
        pending.terminal_reason = "wall-clock pending deadline reached"
    return pending


def on_candle(pending: Pending, candle_time: datetime, technical_invalid: bool) -> Pending:
    """Process a closed candle only if the wall-clock poll did not expire it."""
    poll_wall_clock(pending, candle_time)
    if pending.terminal_status is not None:
        return pending
    if technical_invalid:
        pending.terminal_status = "INVALIDATED"
        pending.terminal_at = candle_time
        pending.terminal_reason = "technical invalidation before deadline"
    return pending


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def main() -> None:
    start = dt("2026-07-07T20:30:00+00:00")
    tests = []

    # Deadline expires without receiving any candle.
    p = Pending(start)
    poll_wall_clock(p, start + timedelta(minutes=60))
    tests.append(("expires_without_new_candle", p.terminal_status == "EXPIRED"))

    # A late candle with technical invalidation cannot overwrite expiry.
    p = Pending(start)
    poll_wall_clock(p, start + timedelta(minutes=90))
    on_candle(p, start + timedelta(minutes=90), technical_invalid=True)
    tests.append(("late_invalidating_candle_does_not_overwrite_expiry",
                  p.terminal_status == "EXPIRED"))

    # Technical invalidation before deadline still wins.
    p = Pending(start)
    on_candle(p, start + timedelta(minutes=55), technical_invalid=True)
    tests.append(("invalidation_before_deadline", p.terminal_status == "INVALIDATED"))

    # Pending remains actionable just before deadline.
    p = Pending(start)
    poll_wall_clock(p, start + timedelta(minutes=59, seconds=59))
    tests.append(("pending_before_deadline", p.terminal_status is None))

    # A timer/poll after deadline records the actual observed time separately
    # from the immutable deadline.
    p = Pending(start)
    observed = start + timedelta(minutes=65)
    poll_wall_clock(p, observed)
    tests.append(("immutable_deadline", p.deadline == start + timedelta(minutes=60)))
    tests.append(("observed_terminal_time_recorded", p.terminal_at == observed))
    tests.append(("terminal_delay_audited",
                  (p.terminal_at - p.deadline).total_seconds() / 60 == 5))

    # Activated entries are not pending-entry expiries.
    p = Pending(start, entry_activated=True)
    poll_wall_clock(p, start + timedelta(minutes=120))
    tests.append(("activated_entry_not_pending_expired", p.terminal_status is None))

    for name, ok in tests:
        if not ok:
            raise AssertionError(f"FAIL {name}")
        print(f"PASS {name}")
    print(f"PASS all {len(tests)} isolated wall-clock harness tests")
    print("LIMITATION=expiry is observed on the next scheduler poll; not a real-time timer")
    print("SCOPE=research-only harness; live engine/dashboard/cron unchanged; auto-trade disabled")


if __name__ == "__main__":
    main()
