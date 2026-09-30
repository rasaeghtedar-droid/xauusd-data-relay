#!/usr/bin/env python3
"""FVG setups with original nearest structural target, eligible when RR >= 2.0."""
from __future__ import annotations
import json, os
from datetime import timedelta
from pathlib import Path

from fvg_only_gold_hunter_backtest import fvg_at, agg, pt, swings, equals, uniq, PAD, LOOKBACK, load

def target_at_min_rr(direction, entry, risk, ctx, min_rr=2.0):
    levels = uniq(swings(ctx, "high") + equals(ctx, "high")) if direction == "BUY" else uniq(swings(ctx, "low") + equals(ctx, "low"))
    valid = [x for x in levels if x > entry] if direction == "BUY" else [x for x in levels if x < entry]
    ordered = sorted(valid, reverse=direction == "SELL")
    for level in ordered:
        rr = ((level - entry) / risk) if direction == "BUY" else ((entry - level) / risk)
        if rr >= min_rr:
            return level, rr
    return None, None

def score(m5, direction, sl, tp, start):
    for k in range(start + 1, len(m5)):
        b = m5[k]
        sl_hit = b["low"] <= sl if direction == "BUY" else b["high"] >= sl
        tp_hit = b["high"] >= tp if direction == "BUY" else b["low"] <= tp
        if sl_hit and tp_hit:
            return "AMBIGUOUS"
        if tp_hit:
            return "TP"
        if sl_hit:
            return "SL"
    return "OPEN_AT_DATA_END"

def main():
    m5 = load()
    start = os.getenv("FVG_ONLY_START_UTC")
    end = os.getenv("FVG_ONLY_END_UTC")
    if start:
        m5 = [x for x in m5 if pt(x["openTime"]) >= pt(start)]
    if end:
        m5 = [x for x in m5 if pt(x["openTime"]) <= pt(end)]

    m15 = agg(m5, 15)
    setups = []
    pending = None

    for i in range(50, len(m5)):
        c = m5[i]

        if pending is not None:
            f = pending
            invalid = (
                (f["direction"] == "BUY" and c["low"] <= f["lo"]) or
                (f["direction"] == "SELL" and c["high"] >= f["hi"])
            )
            if invalid:
                pending = None
            elif c["low"] <= f["mid"] <= c["high"]:
                confirmed = (
                    (f["direction"] == "BUY" and c["close"] > f["mid"] and c["close"] > c["open"]) or
                    (f["direction"] == "SELL" and c["close"] < f["mid"] and c["close"] < c["open"])
                )
                if confirmed:
                    entry = f["mid"]
                    sl = f["lo"] - PAD if f["direction"] == "BUY" else f["hi"] + PAD
                    risk = (entry - sl) if f["direction"] == "BUY" else (sl - entry)
                    tp, rr = target_at_min_rr(f["direction"], entry, risk, f["ctx"], 2.0)
                    if tp is not None:
                        setups.append({
                                "direction": f["direction"],
                                "formation_time": f["time"],
                                "confirmation_time": c["openTime"],
                                "entry": round(entry, 3),
                                "sl": round(sl, 3),
                                "tp": round(tp, 3),
                                "rr": round(rr, 2),
                                "outcome": score(m5, f["direction"], sl, tp, i),
                            })
                    pending = None

        if pending is None:
            f = fvg_at(m5, i)
            if f:
                t = pt(f["time"])
                ctx = [x for x in m15 if pt(x["openTime"]) <= t - timedelta(minutes=15)][-LOOKBACK:]
                pending = {**f, "ctx": ctx}

    closed = [x for x in setups if x["outcome"] in ("TP", "SL", "AMBIGUOUS")]
    tp = sum(x["outcome"] == "TP" for x in closed)
    sl = sum(x["outcome"] == "SL" for x in closed)
    amb = sum(x["outcome"] == "AMBIGUOUS" for x in closed)
    net = sum(x["rr"] if x["outcome"] == "TP" else -1 if x["outcome"] == "SL" else 0 for x in closed)

    buckets = [
        ("2<=RR<2.5", lambda r: 2 <= r < 2.5),
        ("2.5<=RR<3", lambda r: 2.5 <= r < 3),
        ("3<=RR<4", lambda r: 3 <= r < 4),
        ("4<=RR<5", lambda r: 4 <= r < 5),
        ("RR>=5", lambda r: r >= 5),
    ]

    def bucket(name, pred):
        rows = [x for x in setups if pred(x["rr"])]
        c = [x for x in rows if x["outcome"] in ("TP", "SL", "AMBIGUOUS")]
        t = sum(x["outcome"] == "TP" for x in c)
        s = sum(x["outcome"] == "SL" for x in c)
        a = sum(x["outcome"] == "AMBIGUOUS" for x in c)
        n = sum(x["rr"] if x["outcome"] == "TP" else -1 if x["outcome"] == "SL" else 0 for x in c)
        return {
            "bucket": name, "setups": len(rows), "closed": len(c),
            "tp": t, "sl": s, "ambiguous": a,
            "win_rate_closed": round(100 * t / (t + s), 2) if t + s else None,
            "net_r": round(n, 2), "conservative_net_r": round(n - a, 2),
            "avg_rr": round(sum(x["rr"] for x in rows) / len(rows), 2) if rows else None,
        }

    result = {
        "status": "COMPLETED",
        "validation_start_utc": start,
        "validation_end_utc": end,
        "eligible_setups": len(setups),
        "outcomes": {"tp": tp, "sl": sl, "ambiguous": amb},
        "win_rate_closed": round(100 * tp / (tp + sl), 2) if tp + sl else None,
        "net_r": round(net, 2),
        "conservative_net_r": round(net - amb, 2),
        "avg_rr": round(sum(x["rr"] for x in setups) / len(setups), 2) if setups else None,
        "rr_buckets": [bucket(name, pred) for name, pred in buckets],
        "setups": setups,
        "notes": [
            "All confirmed FVG setups are eligible when the nearest pre-confirmation M15 structural target that satisfies RR >= 2.0 exists.",
            "No upper RR cutoff is applied; the first structural target meeting RR >= 2.0 is used.",
            "No future data is used to choose the target; future candles are used only to score TP/SL outcome.",
            "Diagnostic only; no strategy parameters changed.",
        ],
    }
    out = Path("backtest/fvg_rr2_plus_diagnostics.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
