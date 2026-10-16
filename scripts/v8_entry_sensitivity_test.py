#!/usr/bin/env python3
"""Research-only sensitivity test for V8 Cap4 pending time and FVG entry depth.

This file dynamically compiles an isolated copy of run_v8 from the locked live
module. It does not edit or monkeypatch the imported production function.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path

# GitHub Actions executes this file by path, so ensure repository root is importable.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import fvg_v8_live_dry_run as live
from scripts.fvg_only_gold_hunter_backtest import load, pt


START = os.getenv("FVG_ONLY_START_UTC", "2026-10-15T00:00:00+00:00")
END = os.getenv("FVG_ONLY_END_UTC", "2026-10-16T23:59:59+00:00")
SOURCE = os.getenv("COMBINED_SOURCE_URL")
OUT = Path("backtest/v8_entry_sensitivity_results.json")


def build_variant(expiry_minutes: int, entry_mode: str):
    source = inspect.getsource(live.run_v8)
    source = source.replace(
        "def run_v8(m5: list[dict])",
        "def run_v8_variant(m5: list[dict])",
        1,
    )
    source = source.replace(
        "PENDING_ENTRY_MAX_MINUTES",
        "_VARIANT_PENDING_ENTRY_MAX_MINUTES",
    )
    if entry_mode == "midpoint":
        pass
    elif entry_mode == "shallow":
        old = 'entry = f["mid"]'
        new = 'entry = f["lo"] + (f["hi"] - f["lo"]) * (0.75 if d == "BUY" else 0.25)'
        if old not in source:
            raise RuntimeError("Could not locate midpoint entry expression; refusing silent test drift")
        source = source.replace(old, new)
    else:
        raise ValueError(f"Unknown entry mode: {entry_mode}")

    namespace = dict(live.__dict__)
    namespace["_VARIANT_PENDING_ENTRY_MAX_MINUTES"] = expiry_minutes
    exec(compile(source, f"<V8 research variant {expiry_minutes}/{entry_mode}>", "exec"), namespace)
    return namespace["run_v8_variant"]


def summarize(trades: list[dict]) -> dict:
    tp = sum(t.get("status") == "TP" for t in trades)
    sl = sum(t.get("status") == "SL" for t in trades)
    ambiguous = sum(t.get("status") == "AMBIGUOUS" for t in trades)
    expired = sum(t.get("status") == "EXPIRED" for t in trades)
    invalidated = sum(t.get("status") == "INVALIDATED" for t in trades)
    pending = sum(t.get("status") in ("PENDING", "ACTIVE") for t in trades)
    # Count every lifecycle outcome explicitly. A setup cancelled before Entry
    # is not a trade loss; it contributes 0R and remains visible in the audit.
    status_counts = {}
    for trade in trades:
        status = str(trade.get("status") or "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1
    wins_r = sum(float(t.get("rr") or 0) for t in trades if t.get("status") == "TP")
    losses_r = sum(abs(float(t.get("result_r", -1) or 0)) for t in trades if t.get("status") == "SL")
    net_r = wins_r - losses_r
    resolved = tp + sl
    return {
        "signals": len(trades),
        "tp": tp,
        "sl": sl,
        "ambiguous": ambiguous,
        "expired_no_entry": expired,
        "invalidated_before_entry": invalidated,
        "still_pending_or_active": pending,
        "status_counts": status_counts,
        "non_trade_cancellations_zero_r": expired + invalidated,
        "resolved_trades": resolved,
        "win_rate_percent": round(100 * tp / resolved, 2) if resolved else None,
        "net_r": round(net_r, 2),
        "avg_rr_of_tp": round(wins_r / tp, 2) if tp else None,
    }


def main():
    if not SOURCE and not Path(os.getenv("V8_SENSITIVITY_DATA_JSON", "data/xauusd_analysis.json")).exists():
        raise RuntimeError("Either local relay JSON or COMBINED_SOURCE_URL is required")
    local_json = os.getenv("V8_SENSITIVITY_DATA_JSON", "data/xauusd_analysis.json")
    local_path = Path(local_json)
    if local_path.exists():
        market = json.loads(local_path.read_text(encoding="utf-8"))
        raw_bars = market.get("intervals", {}).get("5m", {}).get("bars", [])
        bars = [
            {"openTime": str(b["openTime"]), "open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]), "close": float(b["close"])}
            for b in raw_bars if b.get("openTime") and b.get("isOpen") is False
        ]
        bars.sort(key=lambda b: b["openTime"])
        print(f"DATA_SOURCE=local relay JSON ({len(bars)} M5 candles)")
    else:
        bars = load()
        print(f"DATA_SOURCE=historical CSV ({len(bars)} M5 candles)")
    # Keep warm-up bars before START so swings/FVG context are not truncated.
    end_dt = pt(END)
    bars = [b for b in bars if pt(b["openTime"]) <= end_dt]
    if not bars:
        raise RuntimeError("No candles available in requested window")
    first_test_idx = next((i for i, b in enumerate(bars) if pt(b["openTime"]) >= pt(START)), None)
    if first_test_idx is None or first_test_idx < 100:
        raise RuntimeError(f"Insufficient warm-up/history before test start: index={first_test_idx}; need at least 100 bars")
    # Run the state machine on full warm-up data, but only count lifecycle events
    # confirmed inside the requested test interval.
    variants = [
        (60, "midpoint", "baseline_mid_60m"),
        (90, "midpoint", "midpoint_90m"),
        (120, "midpoint", "midpoint_120m"),
        (60, "shallow", "shallow_entry_60m"),
        (90, "shallow", "shallow_entry_90m"),
        (120, "shallow", "shallow_entry_120m"),
    ]
    results = {}
    for expiry, mode, name in variants:
        fn = build_variant(expiry, mode)
        _, _, meta, _, trades = fn(bars)
        selected = [
            t for t in trades
            if t.get("confirmation_time") and pt(t["confirmation_time"]) >= pt(START)
            and pt(t["confirmation_time"]) <= end_dt
        ]
        summary = summarize(selected)
        # Audit candle-level ambiguity: when Entry and TP/SL are first touched
        # inside the same M5 candle, OHLC alone cannot establish the sequence.
        same_bar_entry_exit = [
            t for t in selected
            if t.get("activation_time")
            and t.get("exit_time") == t.get("activation_time")
            and t.get("status") in ("TP", "SL", "AMBIGUOUS")
        ]
        late_pending_terminal = []
        for t in selected:
            if t.get("activation_time") is not None or t.get("status") not in ("EXPIRED", "INVALIDATED"):
                continue
            age = (pt(t["exit_time"]) - pt(t["confirmation_time"])).total_seconds() / 60.0
            if age > expiry:
                late_pending_terminal.append({
                    "confirmation_time": t.get("confirmation_time"),
                    "status": t.get("status"),
                    "exit_time": t.get("exit_time"),
                    "age_minutes": round(age, 2),
                    "expiry_reason": t.get("expiry_reason"),
                    "audit_note": "Terminal event occurred after configured timeout; inspect timeout-vs-invalidation priority and missing-candle gap."
                })
        summary["same_m5_bar_entry_and_exit"] = len(same_bar_entry_exit)
        summary["pending_terminal_events_after_timeout"] = len(late_pending_terminal)
        results[name] = {
            "entry_mode": mode,
            "pending_entry_minutes": expiry,
            "summary": summary,
            "same_bar_entry_exit_examples": same_bar_entry_exit,
            "pending_terminal_events_after_timeout": late_pending_terminal,
            "sample_trades": [
                {k: t.get(k) for k in (
                    "confirmation_time", "direction", "entry", "sl", "tp", "rr",
                    "status", "activation_time", "exit_time", "expiry_reason", "result_r"
                )}
                for t in selected
            ],
        }
        print(f"{name}: {json.dumps(results[name]['summary'], sort_keys=True)}")

    output = {
        "status": "COMPLETED",
        "research_only": True,
        "production_engine_modified": False,
        "auto_trade": "DISABLED",
        "source": SOURCE,
        "window_utc": {"start": START, "end": END},
        "data": {
            "m5_bars_including_warmup": len(bars),
            "first_m5": bars[0]["openTime"],
            "last_m5": bars[-1]["openTime"],
            "warmup_bars_before_test": first_test_idx,
            "minimum_warmup_required": 100,
        },
        "method": {
            "baseline": "locked V8 Cap4 midpoint entry, 60-minute pending validity",
            "alternatives": "same state machine/targets/SL/RR filter; only pending timeout or entry fraction changes",
            "shallow_entry": "BUY at 75% of FVG width from low; SELL at 25% from low; SL stays beyond original FVG boundary",
            "limitations": "Research sensitivity only; entry depth changes actual risk and RR, so TP is recomputed using the locked Cap4 target function and RR>=2 filter. Expired and invalidated signals are counted separately as zero-R non-trades, not as SLs.",
            "lifecycle_audit": "Reports each signal status, technical invalidation reason, expiry reason, activation time and exit time to audit pending setup cancellation.",
        },
        "variants": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print("RESULT_JSON=" + str(OUT))
    print(json.dumps({"status": output["status"], "data": output["data"],
                      "summaries": {k: v["summary"] for k, v in results.items()}}, indent=2))


if __name__ == "__main__":
    main()
