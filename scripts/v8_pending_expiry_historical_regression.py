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


def build_replay_function(expiry_first: bool = False):
    # Use the exact same pre-fix V8 engine source for both runs. The candidate
    # differs by one isolated policy change only: at/after the 60-minute
    # deadline, EXPIRED wins over a technical invalidation on the late candle.
    raw = subprocess.check_output(
        ["git", "show", "refs/remotes/origin/experiment-fvg-setups:scripts/fvg_v8_live_dry_run.py"],
        cwd=ROOT, text=True,
    )
    start = raw.index("def run_v8(")
    end = raw.index("\ndef merge_into_analysis", start)
    source = raw[start:end]

    if expiry_first:
        block_start = source.index('            if not active.get("entry_activated", False):')
        block_end = source.index('            if active is not None:\n                sl_hit =', block_start)
        isolated_block = '''            if not active.get("entry_activated", False):
                confirmation_dt = pt(active["confirmation_time"])
                current_dt = pt(c["openTime"])
                pending_age_minutes = (current_dt - confirmation_dt).total_seconds() / 60.0

                # Isolated candidate policy: deadline takes priority at/after 60m.
                if pending_age_minutes >= PENDING_ENTRY_MAX_MINUTES:
                    latest_result = {
                        "status": "EXPIRED",
                        "icon": "⌛",
                        "signal_key": f'{active["direction"]}|{active["confirmation_time"]}',
                        "direction": active["direction"],
                        "entry": active["entry"],
                        "sl": active["sl"],
                        "tp": active["tp"],
                        "rr": active["rr"],
                        "result_r": 0.0,
                        "activation_time": None,
                        "exit_time": c["openTime"],
                        "expiry_reason": "Entry not touched within 60 minutes",
                    }
                    if active_history is not None:
                        active_history.update(latest_result)
                        trade_history.append(dict(active_history))
                    else:
                        trade_history.append(dict(latest_result))
                    active = None
                    active_history = None
                else:
                    technical_invalid = (
                        c["close"] < active["zone_lo"]
                        if active["direction"] == "BUY"
                        else c["close"] > active["zone_hi"]
                    )
                    if technical_invalid:
                        latest_result = {
                            "status": "INVALIDATED",
                            "icon": "⚠️",
                            "signal_key": f'{active["direction"]}|{active["confirmation_time"]}',
                            "direction": active["direction"],
                            "entry": active["entry"],
                            "sl": active["sl"],
                            "tp": active["tp"],
                            "rr": active["rr"],
                            "result_r": 0.0,
                            "activation_time": None,
                            "exit_time": c["openTime"],
                            "expiry_reason": "technical invalidation: closed M5 candle crossed the FVG boundary",
                        }
                        if active_history is not None:
                            active_history.update(latest_result)
                            trade_history.append(dict(active_history))
                        else:
                            trade_history.append(dict(latest_result))
                        active = None
                        active_history = None
                        continue

                    entry_hit = (
                        c["low"] <= active["entry"]
                        if active["direction"] == "BUY"
                        else c["high"] >= active["entry"]
                    )
                    if entry_hit:
                        active["entry_activated"] = True
                        active["activation_time"] = c["openTime"]
                        if active_history is not None:
                            active_history["status"] = "ACTIVE"
                            active_history["activation_time"] = c["openTime"]
                    if not active.get("entry_activated", False):
                        continue
'''
        source = source[:block_start] + isolated_block + source[block_end:]

    namespace = dict(candidate.__dict__)
    label = "isolated expiry-first policy" if expiry_first else "baseline V8"
    exec(compile(source, f"<{label} run_v8>", "exec"), namespace)
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
    baseline_fn = build_replay_function(expiry_first=False)
    candidate_fn = build_replay_function(expiry_first=True)

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

    baseline_only_keys = sorted(set(base) - set(cand))
    candidate_only_keys = sorted(set(cand) - set(base))
    def compact_record(trade: dict) -> dict:
        fields = (
            "signal_key", "direction", "confirmation_time", "status",
            "activation_time", "exit_time", "result_r", "entry", "sl", "tp",
            "invalidation_reason", "reason", "pending_expiry_minutes",
        )
        return {field: trade.get(field) for field in fields if field in trade}

    baseline_only_details = [
        {"signal_key": key, **compact_record(base[key])}
        for key in baseline_only_keys
    ]
    candidate_only_details = [
        {"signal_key": key, **compact_record(cand[key])}
        for key in candidate_only_keys
    ]

    status_changes = []
    for key in sorted(set(base) & set(cand)):
        if base[key].get("status") != cand[key].get("status"):
            status_changes.append({
                "signal_key": key,
                "baseline_status": base[key].get("status"),
                "candidate_status": cand[key].get("status"),
            })

    # Audit the four expected INVALIDATED -> EXPIRED transitions against
    # the actual M5 timeline. This distinguishes the contractual deadline from
    # the first candle on which the replay could observe/record termination.
    bar_times = sorted({
        pt(bar["openTime"]) for bar in bars
        if isinstance(bar, dict) and bar.get("openTime")
    })
    terminal_timing_audit = []
    for change in status_changes:
        key = change["signal_key"]
        before = base.get(key, {})
        after = cand.get(key, {})
        confirmation = before.get("confirmation_time") or after.get("confirmation_time")
        terminal_time = after.get("exit_time") or before.get("exit_time")
        item = {
            "signal_key": key,
            "baseline_status": change["baseline_status"],
            "candidate_status": change["candidate_status"],
            "confirmation_time": confirmation,
            "baseline_terminal_time": before.get("exit_time"),
            "candidate_terminal_time": after.get("exit_time"),
        }
        try:
            if confirmation:
                deadline = pt(confirmation) + __import__("datetime").timedelta(
                    minutes=candidate.PENDING_ENTRY_MAX_MINUTES
                )
                item["expected_deadline"] = deadline.isoformat()
                if terminal_time:
                    observed = pt(terminal_time)
                    item["observed_terminal_time"] = observed.isoformat()
                    item["minutes_after_deadline"] = round(
                        (observed - deadline).total_seconds() / 60, 2
                    )
                    prior = [stamp for stamp in bar_times if stamp < observed]
                    item["previous_available_m5"] = prior[-1].isoformat() if prior else None
                    item["previous_to_terminal_gap_minutes"] = round(
                        (observed - prior[-1]).total_seconds() / 60, 2
                    ) if prior else None
                    item["timing_classification"] = (
                        "ON_OR_BEFORE_DEADLINE" if observed <= deadline
                        else "AFTER_DEADLINE_WITH_CANDLE_GAP"
                        if prior and (observed - prior[-1]).total_seconds() > 5 * 60
                        else "AFTER_DEADLINE_NEXT_AVAILABLE_CANDLE"
                    )
        except (TypeError, ValueError, KeyError):
            item["timing_classification"] = "TIMESTAMP_UNAVAILABLE"
        terminal_timing_audit.append(item)

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
        "baseline_only_signal_count": len(baseline_only_keys),
        "baseline_only_signals": baseline_only_details[:20],
        "candidate_only_signal_count": len(candidate_only_keys),
        "candidate_only_signals": candidate_only_details[:20],
        "status_change_count": len(status_changes),
        "status_changes": status_changes[:20],
        "terminal_timing_audit": terminal_timing_audit[:20],
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
