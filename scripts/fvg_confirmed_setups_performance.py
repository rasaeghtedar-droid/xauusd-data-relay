#!/usr/bin/env python3
"""Backtest the independently generated FVG-confirmed setups.
Research only. This diagnostic does not change the baseline strategy.
"""
from __future__ import annotations
import json, os
from pathlib import Path
from datetime import timedelta
from fvg_only_gold_hunter_backtest import load, agg, fvg_at, target, pt, PAD

MIN_RR, MAX_RR = 2.0, 2.5

def outcome(m5, direction, sl, tp, start):
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

    confirmations = []
    for i in range(50, len(m5)):
        f = fvg_at(m5, i)
        if not f:
            continue
        t = pt(f["time"])
        ctx = [x for x in m15 if pt(x["openTime"]) <= t - timedelta(minutes=15)][-30:]
        for j in range(i + 1, len(m5)):
            c = m5[j]
            invalid = (
                f["direction"] == "BUY" and c["low"] <= f["lo"]
            ) or (
                f["direction"] == "SELL" and c["high"] >= f["hi"]
            )
            if invalid:
                break
            if c["low"] <= f["mid"] <= c["high"]:
                confirmed = (
                    f["direction"] == "BUY"
                    and c["close"] > f["mid"]
                    and c["close"] > c["open"]
                ) or (
                    f["direction"] == "SELL"
                    and c["close"] < f["mid"]
                    and c["close"] < c["open"]
                )
                if not confirmed:
                    continue
                entry = f["mid"]
                sl = f["lo"] - PAD if f["direction"] == "BUY" else f["hi"] + PAD
                tp = target(f["direction"], entry, sl, ctx)
                rr = None
                out = "TARGET_MISSING"
                if tp is not None:
                    rr = (
                        (tp - entry) / (entry - sl)
                        if f["direction"] == "BUY"
                        else (entry - tp) / (sl - entry)
                    )
                    if MIN_RR <= rr <= MAX_RR:
                        out = outcome(m5, f["direction"], sl, tp, j)
                confirmations.append({
                    "direction": f["direction"],
                    "formation_time": f["time"],
                    "confirmation_time": c["openTime"],
                    "entry": round(entry, 3),
                    "sl": round(sl, 3),
                    "tp": round(tp, 3) if tp is not None else None,
                    "rr": round(rr, 2) if rr is not None else None,
                    "outcome": out,
                })
                break

    eligible = [x for x in confirmations if x["outcome"] in ("TP", "SL", "AMBIGUOUS", "OPEN_AT_DATA_END")]
    tp = sum(x["outcome"] == "TP" for x in eligible)
    sl = sum(x["outcome"] == "SL" for x in eligible)
    amb = sum(x["outcome"] == "AMBIGUOUS" for x in eligible)
    open_ = sum(x["outcome"] == "OPEN_AT_DATA_END" for x in eligible)
    net = sum(x["rr"] if x["outcome"] == "TP" else -1 if x["outcome"] == "SL" else 0 for x in eligible)
    result = {
        "status": "COMPLETED",
        "validation_start_utc": start,
        "validation_end_utc": end,
        "confirmation_count": len(confirmations),
        "target_missing": sum(x["outcome"] == "TARGET_MISSING" for x in confirmations),
        "eligible_setups": len(eligible),
        "outcomes": {"tp": tp, "sl": sl, "ambiguous": amb, "open_at_data_end": open_},
        "win_rate_closed": round(100 * tp / (tp + sl), 2) if tp + sl else None,
        "net_r": round(net, 2),
        "conservative_net_r": round(net - amb, 2),
        "avg_rr_all_eligible": round(sum(x["rr"] for x in eligible if x["rr"] is not None) / len([x for x in eligible if x["rr"] is not None]), 2) if any(x["rr"] is not None for x in eligible) else None,
        "by_direction": {},
        "setups": eligible,
        "notes": [
            "Independent FVG lifecycle; overlapping FVGs are allowed.",
            "Only setups with the existing target engine and RR 2.0-2.5 are included in performance.",
            "Research diagnostic only; baseline strategy unchanged.",
        ],
    }
    for d in ("BUY", "SELL"):
        ss = [x for x in eligible if x["direction"] == d]
        w = sum(x["outcome"] == "TP" for x in ss)
        l = sum(x["outcome"] == "SL" for x in ss)
        result["by_direction"][d] = {
            "signals": len(ss),
            "tp": w,
            "sl": l,
            "ambiguous": sum(x["outcome"] == "AMBIGUOUS" for x in ss),
            "open_at_data_end": sum(x["outcome"] == "OPEN_AT_DATA_END" for x in ss),
            "win_rate_closed": round(100 * w / (w + l), 2) if w + l else None,
            "net_r": round(sum(x["rr"] if x["outcome"] == "TP" else -1 if x["outcome"] == "SL" else 0 for x in ss), 2),
        }
    Path("backtest").mkdir(exist_ok=True)
    Path("backtest/fvg_confirmed_setups_performance.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
