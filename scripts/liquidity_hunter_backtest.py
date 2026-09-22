#!/usr/bin/env python3
"""Replay the Liquidity Hunter engine on archived closed candles."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history"
OUT = ROOT / "backtest" / "liquidity_hunter_results.json"


def load(name):
    return json.loads((HISTORY / name).read_text(encoding="utf-8"))


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_engine():
    path = ROOT / "liquidity_hunter" / "liquidity_hunter.py"
    spec = importlib.util.spec_from_file_location("lh", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def evaluate_outcome(signal, future_m5):
    entry, sl, tp = signal["entry"], signal["sl"], signal["tp"]
    direction = signal["signal"]

    for bar in future_m5:
        hit_sl = bar["low"] <= sl if direction == "BUY" else bar["high"] >= sl
        hit_tp = bar["high"] >= tp if direction == "BUY" else bar["low"] <= tp
        if hit_sl and hit_tp:
            return "AMBIGUOUS_SAME_CANDLE"
        if hit_tp:
            return "TP"
        if hit_sl:
            return "SL"
    return "OPEN_AT_DATA_END"


def main():
    m5 = [x for x in load("xauusd_5m.json") if x.get("isOpen") is False]
    m15 = [x for x in load("xauusd_15m.json") if x.get("isOpen") is False]
    h1 = [x for x in load("xauusd_1h.json") if x.get("isOpen") is False]

    m5.sort(key=lambda x: x["openTime"])
    m15.sort(key=lambda x: x["openTime"])
    h1.sort(key=lambda x: x["openTime"])

    engine = load_engine()
    signals = []
    reason_counts = {}

    for i in range(len(m5)):
        current = m5[i]
        t = parse_time(current["openTime"])

        # Only expose candles whose full interval has closed by this M5 candle.
        m15_cut = t - timedelta(minutes=15)
        h1_cut = t - timedelta(hours=1)
        ctx15 = [x for x in m15 if parse_time(x["openTime"]) <= m15_cut]
        ctx1 = [x for x in h1 if parse_time(x["openTime"]) <= h1_cut]
        ctx5 = m5[: i + 1]

        result = engine.analyze({
            "intervals": {
                "5m": {"bars": ctx5},
                "15m": {"bars": ctx15},
                "1h": {"bars": ctx1},
            }
        })

        status = result.get("status", "UNKNOWN")
        if status == "SETUP FOUND":
            outcome = evaluate_outcome(result, m5[i + 1 :])
            signals.append({**result, "outcome": outcome})
        else:
            reason = result.get("reason", "unknown")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    outcomes = {}
    for s in signals:
        outcomes[s["outcome"]] = outcomes.get(s["outcome"], 0) + 1

    completed = sum(v for k, v in outcomes.items() if k in {"TP", "SL", "AMBIGUOUS_SAME_CANDLE"})
    wins = outcomes.get("TP", 0)
    losses = outcomes.get("SL", 0)

    result = {
        "status": "COMPLETED",
        "data": {
            "m5": len(m5),
            "m15": len(m15),
            "h1": len(h1),
            "first_m5": m5[0]["openTime"] if m5 else None,
            "last_m5": m5[-1]["openTime"] if m5 else None,
        },
        "signals": len(signals),
        "outcomes": outcomes,
        "completed_trades": completed,
        "tp_count": wins,
        "sl_count": losses,
        "ambiguous_count": outcomes.get("AMBIGUOUS_SAME_CANDLE", 0),
        "win_rate_excluding_ambiguous": (wins / (wins + losses) * 100) if wins + losses else None,
        "no_trade_reasons": reason_counts,
        "signal_details": signals,
        "notes": [
            "Replay uses only information available at each closed M5 candle.",
            "15m/H1 candles are included only after their full interval has closed.",
            "No broker execution, spread, slippage, or position sizing is simulated.",
            "If SL and TP occur in the same future M5 candle, the result is marked ambiguous rather than choosing a winner.",
            "This dataset is short and is not sufficient to establish long-run strategy performance."
        ],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
