#!/usr/bin/env python3
"""Regression tests for the candidate V8 pending-expiry branch.

Uses temporary files only; never touches repository data/state files.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fvg_v8_live_dry_run import expire_persisted_pending_state


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def main() -> None:
    confirmed = dt("2026-07-07T20:30:00+00:00")
    deadline = confirmed + timedelta(minutes=60)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        history_path = root / "history.json"
        active_path = root / "active.json"
        write_json(history_path, [{
            "signal_key": "BUY|2026-07-07T20:30:00Z",
            "direction": "BUY",
            "confirmation_time": "2026-07-07T20:30:00Z",
            "status": "PENDING",
            "activation_time": None,
            "pending_expiry_minutes": 60,
        }])
        write_json(active_path, {
            "status": "SIGNAL",
            "signal_key": "BUY|2026-07-07T20:30:00Z",
            "signal": {"direction": "BUY", "confirmation_time": "2026-07-07T20:30:00Z"},
        })

        count = expire_persisted_pending_state(history_path, active_path, now=deadline)
        history = json.loads(history_path.read_text())
        state = json.loads(active_path.read_text())
        assert count == 1
        assert history[0]["status"] == "EXPIRED"
        assert history[0]["exit_time"] == "2026-07-07T21:30:00Z"
        assert history[0]["expiry_detection_delay_seconds"] == 0
        assert state["status"] == "NO TRADE"
        print("PASS expires_persisted_pending_at_deadline_and_clears_active_state")

        count = expire_persisted_pending_state(history_path, active_path, now=deadline + timedelta(minutes=30))
        history2 = json.loads(history_path.read_text())
        assert count == 0
        assert history2[0]["status"] == "EXPIRED"
        assert history2[0]["exit_time"] == "2026-07-07T21:30:00Z"
        print("PASS terminal_expiry_is_idempotent")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        history_path = root / "history.json"
        active_path = root / "active.json"
        write_json(history_path, [
            {"signal_key": "BUY|activated", "confirmation_time": "2026-07-07T20:30:00Z",
             "status": "ACTIVE", "activation_time": "2026-07-07T20:40:00Z"},
            {"signal_key": "BUY|already-expired", "confirmation_time": "2026-07-07T20:30:00Z",
             "status": "EXPIRED", "activation_time": None, "exit_time": "2026-07-07T21:30:00Z"},
        ])
        count = expire_persisted_pending_state(history_path, active_path, now=deadline + timedelta(hours=2))
        history = json.loads(history_path.read_text())
        assert count == 0
        assert [x["status"] for x in history] == ["ACTIVE", "EXPIRED"]
        print("PASS activated_and_terminal_records_are_not_rewritten")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        history_path = root / "history.json"
        active_path = root / "active.json"
        write_json(history_path, [{
            "signal_key": "SELL|late",
            "confirmation_time": "2026-07-07T20:30:00Z",
            "status": "PENDING",
            "activation_time": None,
            "pending_expiry_minutes": 60,
        }])
        count = expire_persisted_pending_state(
            history_path, active_path, now=deadline + timedelta(minutes=5)
        )
        history = json.loads(history_path.read_text())
        assert count == 1
        assert history[0]["status"] == "EXPIRED"
        assert history[0]["exit_time"] == "2026-07-07T21:30:00Z"
        assert history[0]["expiry_detection_delay_seconds"] == 300
        print("PASS delayed_poll_records_deadline_and_detection_delay")

    print("PASS all 4 candidate engine regression tests")
    print("SCOPE=candidate branch only; production branch unchanged; auto-trade disabled")


if __name__ == "__main__":
    main()
