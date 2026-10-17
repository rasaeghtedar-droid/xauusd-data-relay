"""Pending-expiry policy helper for experimental V8 branch.

This helper operates on persisted dashboard/history dictionaries only. It is
not used by production workflows unless the experimental branch is explicitly
selected and deployed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def expire_persisted_pending(history: list[dict], now: datetime, timeout_minutes: int = 60) -> tuple[list[dict], list[str]]:
    """Mark unactivated pending records expired based on wall clock, idempotently."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if timeout_minutes <= 0:
        raise ValueError("timeout_minutes must be positive")

    now = now.astimezone(timezone.utc)
    changed: list[str] = []
    for item in history:
        if item.get("status") != "PENDING":
            continue
        if item.get("activation_time"):
            continue
        confirmation = parse_utc(item.get("confirmation_time"))
        if confirmation is None:
            continue
        deadline = confirmation + timedelta(minutes=timeout_minutes)
        if now < deadline:
            continue
        item["status"] = "EXPIRED"
        item["result_r"] = 0.0
        item["exit_time"] = deadline.isoformat().replace("+00:00", "Z")
        item["expiry_detected_at"] = now.isoformat().replace("+00:00", "Z")
        item["expiry_reason"] = "wall-clock pending deadline reached; entry was not activated"
        changed.append(str(item.get("signal_key") or ""))
    return history, changed


def pending_signal_is_actionable(signal: dict | None, now: datetime, timeout_minutes: int = 60) -> bool:
    if not isinstance(signal, dict):
        return False
    if signal.get("activation_time"):
        return True
    confirmation = parse_utc(signal.get("confirmation_time"))
    if confirmation is None:
        return False
    return now.astimezone(timezone.utc) < confirmation + timedelta(minutes=timeout_minutes)
