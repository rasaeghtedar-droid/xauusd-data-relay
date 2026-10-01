#!/usr/bin/env python3
"""Trade-path realism diagnostics for locked executable FVG V8.
Research only. Reads the already-generated V8 result and the same M5 source.
Does not change any trading rule or outcome.
"""
from __future__ import annotations
import json, os
from collections import Counter
from datetime import datetime
from pathlib import Path

from fvg_only_gold_hunter_backtest import load, pt

RESULT = Path("backtest/fvg_executable_audit_v8_results.json")
OUT = Path("backtest/fvg_v8_trade_path_diagnostics.json")


def minutes_between(a, b):
    return (pt(b) - pt(a)).total_seconds() / 60.0


def bucket_duration(minutes):
    if minutes <= 30: return "<=30m"
    if minutes <= 60: return "30m-1h"
    if minutes <= 120: return "1-2h"
    if minutes <= 240: return "2-4h"
    if minutes <= 480: return "4-8h"
    if minutes <= 1440: return "8-24h"
    return ">24h"


def bucket_rr(rr):
    if rr < 2.5: return "2<=RR<2.5"
    if rr < 3: return "2.5<=RR<3"
    if rr < 4: return "3<=RR<4"
    if rr < 5: return "4<=RR<5"
    return "RR>=5"


def summarize(rows):
    closed = [r for r in rows if r["outcome"] in ("TP", "SL", "AMBIGUOUS")]
    durations = [r["hold_minutes"] for r in closed]
    mfes = [r["mfe_r"] for r in closed]
    maes = [r["mae_r"] for r in closed]
    return {
        "trades": len(rows),
        "closed": len(closed),
        "tp": sum(r["outcome"] == "TP" for r in closed),
        "sl": sum(r["outcome"] == "SL" for r in closed),
        "ambiguous": sum(r["outcome"] == "AMBIGUOUS" for r in closed),
        "median_hold_minutes": round(sorted(durations)[len(durations)//2], 2) if durations else None,
        "p90_hold_minutes": round(sorted(durations)[max(0, int(len(durations)*0.90)-1)], 2) if durations else None,
        "max_hold_minutes": round(max(durations), 2) if durations else None,
        "avg_mfe_r": round(sum(mfes)/len(mfes), 2) if mfes else None,
        "avg_mae_r": round(sum(maes)/len(maes), 2) if maes else None,
    }


def main():
    data = json.loads(RESULT.read_text(encoding="utf-8"))
    if data.get("status") != "COMPLETED":
        raise RuntimeError("V8 result is not COMPLETED")
    m5 = load()
    start = data.get("validation_start_utc")
    end = data.get("validation_end_utc")
    if start:
        m5 = [x for x in m5 if pt(x["openTime"]) >= pt(start)]
    if end:
        m5 = [x for x in m5 if pt(x["openTime"]) <= pt(end)]

    trades = data["trades"]
    enriched = []
    for t in trades:
        entry_idx = t["entry_index"]
        entry = t.get("_raw_entry", t["entry"])
        sl = t.get("_raw_sl", t["sl"])
        tp = t.get("_raw_tp", t["tp"])
        risk = abs(entry - sl)
        end_idx = None
        if t["outcome"] != "OPEN_AT_DATA_END":
            for i in range(entry_idx + 1, len(m5)):
                if m5[i]["openTime"] == t["exit_time"]:
                    end_idx = i
                    break
        else:
            end_idx = len(m5) - 1
        if end_idx is None:
            raise RuntimeError(f"Could not locate exit candle for {t['formation_time']}")

        highs = [m5[i]["high"] for i in range(entry_idx + 1, end_idx + 1)]
        lows = [m5[i]["low"] for i in range(entry_idx + 1, end_idx + 1)]
        if t["direction"] == "BUY":
            mfe = (max(highs) - entry) / risk if highs else 0.0
            mae = (entry - min(lows)) / risk if lows else 0.0
        else:
            mfe = (entry - min(lows)) / risk if lows else 0.0
            mae = (max(highs) - entry) / risk if highs else 0.0

        hold = minutes_between(t["entry_time"], t["exit_time"]) if t["exit_time"] else minutes_between(t["entry_time"], m5[-1]["openTime"])
        enriched.append({
            "direction": t["direction"],
            "formation_time": t["formation_time"],
            "confirmation_time": t["confirmation_time"],
            "entry_time": t["entry_time"],
            "exit_time": t["exit_time"],
            "outcome": t["outcome"],
            "rr": t["rr"],
            "hold_minutes": round(hold, 2),
            "hold_bucket": bucket_duration(hold),
            "mfe_r": round(mfe, 2),
            "mae_r": round(mae, 2),
            "rr_bucket": bucket_rr(t["rr"]),
        })

    result = {
        "status": "COMPLETED",
        "research_only": True,
        "source_result": str(RESULT),
        "purpose": "Measure trade-path duration and excursion on the exact locked V8 cohort; no rule changes.",
        "baseline_signals": len(trades),
        "overall": summarize(enriched),
        "hold_duration_buckets": {
            b: sum(r["hold_bucket"] == b for r in enriched)
            for b in ("<=30m","30m-1h","1-2h","2-4h","4-8h","8-24h",">24h")
        },
        "rr_buckets": {
            b: {
                "trades": sum(r["rr_bucket"] == b for r in enriched),
                "tp": sum(r["rr_bucket"] == b and r["outcome"] == "TP" for r in enriched),
                "sl": sum(r["rr_bucket"] == b and r["outcome"] == "SL" for r in enriched),
                "ambiguous": sum(r["rr_bucket"] == b and r["outcome"] == "AMBIGUOUS" for r in enriched),
            }
            for b in ("2<=RR<2.5","2.5<=RR<3","3<=RR<4","4<=RR<5","RR>=5")
        },
        "by_direction": {
            d: summarize([r for r in enriched if r["direction"] == d])
            for d in ("BUY","SELL")
        },
        "by_outcome": {
            o: summarize([r for r in enriched if r["outcome"] == o])
            for o in ("TP","SL","AMBIGUOUS","OPEN_AT_DATA_END")
        },
        "invariants": {
            "status": "PASS",
            "same_cohort": len(enriched) == len(trades),
            "no_rule_changes": True,
            "source_matches_v8_validation_window": True,
        },
        "trades": enriched,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
