#!/usr/bin/env python3
"""Replay Liquidity Hunter and separately audit raw M5 liquidity sweeps."""

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


def audit_sweep(engine, m5, m15, h1):
    sweeps = []
    for i in range(30, len(m5)):
        t = parse_time(m5[i]["openTime"])
        # Context only from M15 candles fully closed before this M5 candle.
        ctx15 = [x for x in m15 if parse_time(x["openTime"]) <= t - timedelta(minutes=15)]
        if len(ctx15) < 10:
            continue
        context = ctx15[-30:]
        highs = engine.unique_levels(
            engine.recent_swing_highs(context) + engine.equal_levels(context, "high")
        )
        lows = engine.unique_levels(
            engine.recent_swing_lows(context) + engine.equal_levels(context, "low")
        )
        c = m5[i]
        found = None
        for level in sorted(highs, reverse=True):
            if c["high"] > level and c["close"] < level:
                found = ("SELL", level, "buy-side sweep")
                break
        if not found:
            for level in sorted(lows):
                if c["low"] < level and c["close"] > level:
                    found = ("BUY", level, "sell-side sweep")
                    break
        if found:
            direction, level, typ = found
            conf = engine.m5_confirmation(m5[:i+1], direction)
            sweeps.append({
                "time": c["openTime"],
                "direction": direction,
                "level": round(level, 3),
                "type": typ,
                "close": c["close"],
                "confirmation": conf,
            })
    return sweeps


def main():
    m5 = sorted([x for x in load("xauusd_5m.json") if not x.get("isOpen")], key=lambda x: x["openTime"])
    m15 = sorted([x for x in load("xauusd_15m.json") if not x.get("isOpen")], key=lambda x: x["openTime"])
    h1 = sorted([x for x in load("xauusd_1h.json") if not x.get("isOpen")], key=lambda x: x["openTime"])
    engine = load_engine()

    signals, reasons = [], {}
    for i in range(len(m5)):
        t = parse_time(m5[i]["openTime"])
        ctx15 = [x for x in m15 if parse_time(x["openTime"]) <= t - timedelta(minutes=15)]
        ctx1 = [x for x in h1 if parse_time(x["openTime"]) <= t - timedelta(hours=1)]
        result = engine.analyze({"intervals":{"5m":{"bars":m5[:i+1]},"15m":{"bars":ctx15},"1h":{"bars":ctx1}}})
        if result.get("status") == "SETUP FOUND":
            signals.append({**result, "outcome": evaluate_outcome(result, m5[i+1:])})
        else:
            reason = result.get("reason","unknown")
            reasons[reason] = reasons.get(reason,0)+1

    sweeps = audit_sweep(engine, m5, m15, h1)
    by_conf = {}
    for s in sweeps:
        key = "confirmed" if s["confirmation"] else "unconfirmed"
        by_conf[key] = by_conf.get(key,0)+1

    result = {
        "status":"COMPLETED",
        "data":{"m5":len(m5),"m15":len(m15),"h1":len(h1),
                "first_m5":m5[0]["openTime"] if m5 else None,
                "last_m5":m5[-1]["openTime"] if m5 else None},
        "signals":len(signals),
        "sweep_audit":{"total_sweeps":len(sweeps),"by_confirmation":by_conf,"details":sweeps},
        "no_trade_reasons":reasons,
        "signal_details":signals,
        "notes":[
            "Sweep audit uses only M15 liquidity levels available before each M5 candle.",
            "This is diagnostic; it does not claim long-run profitability.",
            "The dataset is short."
        ]
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
