#!/usr/bin/env python3
"""Independent FVG lifecycle diagnostic.
Research only. Every detected FVG is tracked independently; this does not change
the baseline strategy and is not used for trading signals.
"""
from __future__ import annotations
import json, os
from pathlib import Path
from datetime import timedelta
from fvg_only_gold_hunter_backtest import load, agg, fvg_at, target, pt, PAD

def main():
    m5 = load()
    start = os.getenv("FVG_ONLY_START_UTC")
    end = os.getenv("FVG_ONLY_END_UTC")
    if start:
        m5 = [x for x in m5 if pt(x["openTime"]) >= pt(start)]
    if end:
        m5 = [x for x in m5 if pt(x["openTime"]) <= pt(end)]
    m15 = agg(m5, 15)

    formations = []
    for i in range(50, len(m5)):
        f = fvg_at(m5, i)
        if f:
            t = pt(f["time"])
            ctx = [x for x in m15 if pt(x["openTime"]) <= t - timedelta(minutes=15)][-30:]
            formations.append({**f, "ctx": ctx, "formed_index": i})

    counts = {
        "formed": len(formations),
        "BUY_formed": sum(x["direction"] == "BUY" for x in formations),
        "SELL_formed": sum(x["direction"] == "SELL" for x in formations),
        "invalidated_before_mid": 0,
        "midpoint_touched": 0,
        "direction_confirmed": 0,
        "target_missing": 0,
        "rr_below_min": 0,
        "rr_above_max": 0,
        "eligible_setups": 0,
        "BUY_eligible": 0,
        "SELL_eligible": 0,
        "no_resolution_before_data_end": 0,
    }

    examples = []
    for f in formations:
        resolved = False
        for j in range(f["formed_index"] + 1, len(m5)):
            c = m5[j]
            invalid = (
                f["direction"] == "BUY" and c["low"] <= f["lo"]
            ) or (
                f["direction"] == "SELL" and c["high"] >= f["hi"]
            )
            if invalid:
                counts["invalidated_before_mid"] += 1
                resolved = True
                break

            if c["low"] <= f["mid"] <= c["high"]:
                counts["midpoint_touched"] += 1
                confirmed = (
                    f["direction"] == "BUY"
                    and c["close"] > f["mid"]
                    and c["close"] > c["open"]
                ) or (
                    f["direction"] == "SELL"
                    and c["close"] < f["mid"]
                    and c["close"] < c["open"]
                )
                if confirmed:
                    counts["direction_confirmed"] += 1
                    entry = f["mid"]
                    sl = f["lo"] - PAD if f["direction"] == "BUY" else f["hi"] + PAD
                    tp = target(f["direction"], entry, sl, f["ctx"])
                    if tp is None:
                        counts["target_missing"] += 1
                    else:
                        rr = (
                            (tp - entry) / (entry - sl)
                            if f["direction"] == "BUY"
                            else (entry - tp) / (sl - entry)
                        )
                        if rr < 2.0:
                            counts["rr_below_min"] += 1
                        elif rr > 2.5:
                            counts["rr_above_max"] += 1
                        else:
                            counts["eligible_setups"] += 1
                            counts[f"{f['direction']}_eligible"] += 1
                            if len(examples) < 20:
                                examples.append({
                                    "direction": f["direction"],
                                    "formation_time": f["time"],
                                    "confirmation_time": c["openTime"],
                                    "entry": round(entry, 3),
                                    "sl": round(sl, 3),
                                    "tp": round(tp, 3),
                                    "rr": round(rr, 2),
                                })
                    resolved = True
                    break

        if not resolved:
            counts["no_resolution_before_data_end"] += 1

    result = {
        "status": "COMPLETED",
        "validation_start_utc": start,
        "validation_end_utc": end,
        "counts": counts,
        "examples": examples,
        "notes": [
            "Diagnostic only; baseline strategy rules are unchanged.",
            "Every detected FVG is tracked independently, so overlapping FVGs are allowed in this diagnostic.",
            "Future candles are inspected only to measure lifecycle outcomes; this diagnostic is not a live signal engine.",
            "No parameter tuning.",
        ],
    }
    Path("backtest").mkdir(exist_ok=True)
    Path("backtest/fvg_independent_lifecycle_diagnostics.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
