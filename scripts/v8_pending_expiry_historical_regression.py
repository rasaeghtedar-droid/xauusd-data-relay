#!/usr/bin/env python3
"""Historical baseline-vs-candidate regression for V8 pending-expiry changes.

Runs both run_v8 implementations on the same historical M5 bars. It does not
write relay state, call APIs, send notifications, or execute trades.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import fvg_v8_live_dry_run as candidate
from scripts.fvg_only_gold_hunter_backtest import load, pt


def baseline_run_v8():
    # GitHub Actions checks out the candidate branch into the workspace.
    # Fetch the baseline file from the remote main branch explicitly, since
    # it may not be part of the shallow checkout's local object database.
    raw = subprocess.check_output(
        ["git", "show", "refs/remotes/origin/experiment-fvg-setups:scripts/fvg_v8_live_dry_run.py"],
        cwd=ROOT, text=True,
    )
    start = raw.index("def run_v8(")
    end = raw.index("\ndef merge_into_analysis", start)
    source = raw[start:end]
    namespace = dict(candidate.__dict__)
    exec(compile(source, "<experiment-fvg-setups V8 baseline run_v8>", "exec"), namespace)
    return namespace["run_v8"]


def key_of(t: dict) -> str | None:
    key = t.get("signal_key")
    if key:
        return str(key)
    direction = t.get("direction")
    confirmation = t.get("confirmation_time")
    if direction and confirmation:
        return f"{direction}|{confirmation}"
    return None


def normalized(trades: list[dict]) -> dict[str, dict]:
    out = {}
    for t in trades:
        key = key_of(t)
        if key:
            out[key] = t
    return out


def main() -> None:
    bars = load()
    if len(bars) < 5000:
        raise RuntimeError(f"Insufficient historical bars: {len(bars)}")
    baseline_fn = baseline_run_v8()
    candidate_fn = candidate.run_v8

    _, _, base_meta, _, base_trades = baseline_fn(bars)
    _, _, cand_meta, _, cand_trades = candidate_fn(bars)
    base = normalized(base_trades)
    cand = normalized(cand_trades)

    # Terminal resolved outcomes must remain byte-for-byte equivalent for
    # signals that both versions identify as completed TP/SL/AMBIGUOUS.
    immutable_statuses = {"TP", "SL", "AMBIGUOUS"}
    shared_immutable = sorted(
        key for key in set(base) & set(cand)
        if base[key].get("status") in immutable_statuses
        and cand[key].get("status") in immutable_statuses
    )
    changed_resolved = []
    for key in shared_immutable:
        a, b = base[key], cand[key]
        fields = ("status", "activation_time", "exit_time", "result_r", "entry", "sl", "tp")
        diffs = {field: (a.get(field), b.get(field)) for field in fields if a.get(field) != b.get(field)}
        if diffs:
            changed_resolved.append({"signal_key": key, "differences": diffs})

    # Candidate must never return a still-pending signal whose deadline passed.
    deadline_violations = []
    for key, trade in cand.items():
        if trade.get("status") not in {"PENDING", "ACTIVE"} or trade.get("activation_time"):
            continue
        confirmation = trade.get("confirmation_time")
        if not confirmation:
            continue
        try:
            age_minutes = (pt(cand_meta["last_m5"]) - pt(confirmation)).total_seconds() / 60
        except (KeyError, TypeError, ValueError):
            continue
        if age_minutes >= candidate.PENDING_ENTRY_MAX_MINUTES:
            deadline_violations.append({
                "signal_key": key, "status": trade.get("status"),
                "age_minutes_at_last_bar": round(age_minutes, 2),
            })

    # Candidate's replay history cannot duplicate a signal key.
    duplicate_keys = []
    seen = set()
    for trade in cand_trades:
        key = key_of(trade)
        if key in seen and key:
            duplicate_keys.append(key)
        if key:
            seen.add(key)

    report = {
        "status": "PASS" if not changed_resolved and not deadline_violations and not duplicate_keys else "FAIL",
        "scope": "historical replay only; no API, filesystem relay state, Telegram, dashboard, cron, or orders",
        "bars": len(bars),
        "first_m5": bars[0].get("openTime"),
        "last_m5": bars[-1].get("openTime"),
        "baseline_trade_records": len(base_trades),
        "candidate_trade_records": len(cand_trades),
        "shared_TP_SL_AMBIGUOUS_records_checked": len(shared_immutable),
        "changed_resolved_records": changed_resolved[:20],
        "changed_resolved_record_count": len(changed_resolved),
        "pending_deadline_violations": deadline_violations[:20],
        "pending_deadline_violation_count": len(deadline_violations),
        "duplicate_candidate_signal_keys": sorted(set(duplicate_keys))[:20],
        "duplicate_candidate_signal_key_count": len(set(duplicate_keys)),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
