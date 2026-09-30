#!/usr/bin/env python3
"""FVG nearest-structural-target diagnostic.
Research only: evaluates every independently confirmed FVG using the nearest
pre-confirmation M15 structural target, without imposing the baseline RR 2.0-2.5 filter.
"""
from __future__ import annotations
import json, os
from pathlib import Path
from datetime import timedelta
from fvg_only_gold_hunter_backtest import load, agg, fvg_at, pt, PAD, swings, equals, uniq

def nearest_target(direction, entry, ctx):
    levels = uniq(swings(ctx, "high") + equals(ctx, "high")) if direction == "BUY" else uniq(swings(ctx, "low") + equals(ctx, "low"))
    valid = [x for x in levels if x > entry] if direction == "BUY" else [x for x in levels if x < entry]
    if not valid:
        return None
    return min(valid) if direction == "BUY" else max(valid)

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

    setups = []
    for i in range(50, len(m5)):
        f = fvg_at(m5, i)
        if not f:
            continue
        t = pt(f["time"])
        ctx = [x for x in m15 if pt(x["openTime"]) <= t - timedelta(minutes=15)][-30:]
        for j in range(i + 1, len(m5)):
            c = m5[j]
            invalid = (f["direction"] == "BUY" and c["low"] <= f["lo"]) or (f["direction"] == "SELL" and c["high"] >= f["hi"])
            if invalid:
                break
            if c["low"] <= f["mid"] <= c["high"]:
                confirmed = (
                    f["direction"] == "BUY" and c["close"] > f["mid"] and c["close"] > c["open"]
                ) or (
                    f["direction"] == "SELL" and c["close"] < f["mid"] and c["close"] < c["open"]
                )
                if not confirmed:
                    continue
                entry = f["mid"]
                sl = f["lo"] - PAD if f["direction"] == "BUY" else f["hi"] + PAD
                tp = nearest_target(f["direction"], entry, ctx)
                if tp is None:
                    setups.append({"direction": f["direction"], "formation_time": f["time"], "confirmation_time": c["openTime"], "outcome": "TARGET_MISSING"})
                else:
                    rr = ((tp-entry)/(entry-sl)) if f["direction"] == "BUY" else ((entry-tp)/(sl-entry))
                    out = outcome(m5, f["direction"], sl, tp, j)
                    setups.append({
                        "direction": f["direction"],
                        "formation_time": f["time"],
                        "confirmation_time": c["openTime"],
                        "entry": round(entry,3),
                        "sl": round(sl,3),
                        "tp": round(tp,3),
                        "rr": round(rr,2),
                        "outcome": out,
                    })
                break

    closed = [x for x in setups if x["outcome"] in ("TP","SL","AMBIGUOUS")]
    tp = sum(x["outcome"] == "TP" for x in closed)
    sl = sum(x["outcome"] == "SL" for x in closed)
    amb = sum(x["outcome"] == "AMBIGUOUS" for x in closed)
    net = sum(x["rr"] if x["outcome"] == "TP" else -1 if x["outcome"] == "SL" else 0 for x in closed)
    rrs = [x["rr"] for x in setups if x.get("rr") is not None]
    result = {
        "status":"COMPLETED",
        "validation_start_utc":start,
        "validation_end_utc":end,
        "confirmation_count":len(setups),
        "target_missing":sum(x["outcome"]=="TARGET_MISSING" for x in setups),
        "outcomes":{"tp":tp,"sl":sl,"ambiguous":amb},
        "win_rate_closed":round(100*tp/(tp+sl),2) if tp+sl else None,
        "net_r":round(net,2),
        "conservative_net_r":round(net-amb,2),
        "avg_rr":round(sum(rrs)/len(rrs),2) if rrs else None,
        "rr_below_1":sum(x["rr"]<1 for x in setups if x.get("rr") is not None),
        "rr_1_to_2":sum(1<=x["rr"]<2 for x in setups if x.get("rr") is not None),
        "rr_2_plus":sum(x["rr"]>=2 for x in setups if x.get("rr") is not None),
        "by_direction":{},
        "notes":[
            "Every confirmed FVG is evaluated independently.",
            "Target is the nearest pre-confirmation M15 structural level in the trade direction.",
            "No RR filter is imposed in this diagnostic.",
            "No future data is used to choose the target; future candles are used only to score TP/SL outcome.",
        ],
        "setups":setups,
    }
    for d in ("BUY","SELL"):
        ss=[x for x in setups if x["direction"]==d and x["outcome"]!="TARGET_MISSING"]
        w=sum(x["outcome"]=="TP" for x in ss); l=sum(x["outcome"]=="SL" for x in ss); a=sum(x["outcome"]=="AMBIGUOUS" for x in ss)
        result["by_direction"][d]={
            "signals":len(ss),"tp":w,"sl":l,"ambiguous":a,
            "target_missing":sum(x["direction"]==d and x["outcome"]=="TARGET_MISSING" for x in setups),
            "win_rate_closed":round(100*w/(w+l),2) if w+l else None,
            "net_r":round(sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in ss),2)
        }
    Path("backtest").mkdir(exist_ok=True)
    Path("backtest/fvg_nearest_target_diagnostics.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))

if __name__=="__main__": main()
