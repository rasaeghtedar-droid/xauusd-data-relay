#!/usr/bin/env python3
"""FVG target-engine diagnostics.
Research only. Does not change the baseline strategy.
"""
from __future__ import annotations
import json, os
from pathlib import Path
from datetime import timedelta
from fvg_only_gold_hunter_backtest import load, agg, fvg_at, pt, PAD, swings, equals, uniq

MIN_RR = 2.0
LOOKBACK = 30

def collect_levels(ctx, direction):
    return uniq(swings(ctx, "high") + equals(ctx, "high")) if direction == "BUY" else uniq(swings(ctx, "low") + equals(ctx, "low"))

def main():
    m5 = load()
    start = os.getenv("FVG_ONLY_START_UTC")
    end = os.getenv("FVG_ONLY_END_UTC")
    if start:
        m5 = [x for x in m5 if pt(x["openTime"]) >= pt(start)]
    if end:
        m5 = [x for x in m5 if pt(x["openTime"]) <= pt(end)]
    m15 = agg(m5, 15)

    rows = []
    for i in range(50, len(m5)):
        f = fvg_at(m5, i)
        if not f:
            continue
        t = pt(f["time"])
        ctx = [x for x in m15 if pt(x["openTime"]) <= t - timedelta(minutes=15)][-LOOKBACK:]
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
                risk = entry - sl if f["direction"] == "BUY" else sl - entry
                levels = collect_levels(ctx, f["direction"])
                valid_levels = [x for x in levels if x > entry] if f["direction"] == "BUY" else [x for x in levels if x < entry]
                candidates = []
                for x in valid_levels:
                    rr = (x-entry)/risk if f["direction"] == "BUY" else (entry-x)/risk
                    candidates.append((x, rr))
                candidates.sort(key=lambda z: z[0], reverse=f["direction"]=="SELL")
                eligible = [x for x in candidates if x[1] >= MIN_RR]
                nearest = candidates[0] if candidates else None
                best_rr = max((x[1] for x in candidates), default=None)
                reason = (
                    "NO_LEVEL"
                    if not candidates else
                    "NEAREST_RR_BELOW_2"
                    if nearest[1] < MIN_RR else
                    "NEAREST_RR_BELOW_2"
                    if nearest[1] < MIN_RR and not eligible else
                    "HAS_ELIGIBLE_LEVEL"
                )
                rows.append({
                    "direction": f["direction"],
                    "formation_time": f["time"],
                    "confirmation_time": c["openTime"],
                    "entry": round(entry,3),
                    "sl": round(sl,3),
                    "risk": round(risk,3),
                    "level_count": len(candidates),
                    "nearest_level": round(nearest[0],3) if nearest else None,
                    "nearest_rr": round(nearest[1],2) if nearest else None,
                    "best_rr": round(best_rr,2) if best_rr is not None else None,
                    "eligible_level_count": len(eligible),
                    "reason": reason,
                })
                break

    confirmed = len(rows)
    missing = [r for r in rows if r["eligible_level_count"] == 0]
    stats = {
        "confirmation_count": confirmed,
        "has_eligible_target": confirmed-len(missing),
        "target_missing": len(missing),
        "no_level": sum(r["reason"]=="NO_LEVEL" for r in missing),
        "nearest_rr_below_2": sum(r["reason"]=="NEAREST_RR_BELOW_2" for r in missing),
        "nearest_rr_above_2_5": 0,
        "missing_with_best_rr_ge_2": sum(r["best_rr"] is not None and r["best_rr"] >= MIN_RR for r in missing),
        "missing_with_best_rr_ge_1_5": sum(r["best_rr"] is not None and r["best_rr"] >= 1.5 for r in missing),
        "missing_with_best_rr_ge_1_0": sum(r["best_rr"] is not None and r["best_rr"] >= 1.0 for r in missing),
        "best_rr_avg_missing": round(sum(r["best_rr"] for r in missing if r["best_rr"] is not None)/len([r for r in missing if r["best_rr"] is not None]),2) if any(r["best_rr"] is not None for r in missing) else None,
    }
    result = {
        "status":"COMPLETED",
        "validation_start_utc":start,
        "validation_end_utc":end,
        "stats":stats,
        "missing_examples":missing[:50],
        "notes":[
            "Diagnostic only; baseline target engine is unchanged.",
            "Purpose: distinguish no structural target from target levels below RR 2.0.",
            "Uses the same M15 context, swing/equal-level detector, and entry/SL as baseline FVG logic.",
        ],
    }
    Path("backtest").mkdir(exist_ok=True)
    Path("backtest/fvg_target_diagnostics.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))

if __name__=="__main__": main()
