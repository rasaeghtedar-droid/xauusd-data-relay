#!/usr/bin/env python3
"""Research-only wall-clock pending expiry monitor prototype.

This models a persisted pending signal being checked on each scheduler tick,
even when no new M5 candle is available. It is intentionally isolated from
fvg_v8_live_dry_run.py and performs no network calls or file writes.
The real cron cadence is 5 minutes, so detection is bounded by polling cadence,
not guaranteed to occur at the exact second of the deadline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class PendingSignal:
    confirmation_time: datetime
    timeout_minutes: int = 60
    entry_activated: bool = False
    terminal_status: str | None = None
    terminal_detected_at: datetime | None = None

    @property
    def deadline(self) -> datetime:
        return self.confirmation_time + timedelta(minutes=self.timeout_minutes)


def poll_pending(signal: PendingSignal, now: datetime, technical_invalid: bool = False) -> PendingSignal:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if signal.terminal_status is not None:
        return signal

    # Pending-only wall-clock expiry is checked before candle-based invalidation.
    # If no candle is available, technical_invalid remains False but expiry
    # still proceeds from persisted confirmation_time.
    if not signal.entry_activated and now >= signal.deadline:
        signal.terminal_status = "EXPIRED"
        signal.terminal_detected_at = now
        return signal

    if technical_invalid:
        signal.terminal_status = "INVALIDATED"
        signal.terminal_detected_at = now
    return signal


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def main() -> None:
    start = dt("2026-07-07T20:30:00+00:00")
    tests = []

    # No candle arrives at the deadline: the scheduler still expires the
    # persisted pending signal at the first poll after deadline.
    s = PendingSignal(start)
    poll_pending(s, start + timedelta(minutes=55), technical_invalid=False)
    assert s.terminal_status is None
    poll_pending(s, start + timedelta(minutes=60), technical_invalid=False)
    assert s.terminal_status == "EXPIRED"
    assert s.terminal_detected_at == start + timedelta(minutes=60)
    print("PASS expires_at_deadline_without_new_candle")

    # Five-minute scheduler cadence: next poll after the deadline detects it.
    s = PendingSignal(start)
    poll_pending(s, start + timedelta(minutes=65), technical_invalid=True)
    assert s.terminal_status == "EXPIRED"
    assert s.terminal_detected_at == start + timedelta(minutes=65)
    assert s.terminal_detected_at - s.deadline == timedelta(minutes=5)
    print("PASS expiry_precedes_late_candle_invalidation_on_next_poll")
    print("PASS polling_delay_bounded_by_5m_schedule_in_this_model")

    # Before deadline, a closed candle that invalidates the setup cancels it.
    s = PendingSignal(start)
    poll_pending(s, start + timedelta(minutes=55), technical_invalid=True)
    assert s.terminal_status == "INVALIDATED"
    print("PASS technical_invalidation_before_deadline")

    # Activated signal is no longer a pending-entry timeout candidate.
    s = PendingSignal(start, entry_activated=True)
    poll_pending(s, start + timedelta(minutes=90), technical_invalid=False)
    assert s.terminal_status is None
    print("PASS activated_signal_not_expired_by_pending_entry_timer")

    # Idempotency: once terminal, repeated polling cannot change the result.
    s = PendingSignal(start)
    poll_pending(s, start + timedelta(minutes=60), technical_invalid=False)
    assert s.terminal_status == "EXPIRED"
    old_detected = s.terminal_detected_at
    poll_pending(s, start + timedelta(minutes=120), technical_invalid=True)
    assert s.terminal_status == "EXPIRED" and s.terminal_detected_at == old_detected
    print("PASS repeated_poll_does_not_change_terminal_expiry")
    print("PASS all 5 wall-clock monitor prototype tests")
    print("LIMIT=5-minute scheduler means expiry detection is not guaranteed at exact second")
    print("SCOPE=prototype only; live engine/dashboard/cron unchanged; auto-trade disabled")


if __name__ == "__main__":
    main()
