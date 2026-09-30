#!/usr/bin/env python3
"""Combined Gold Hunter OOS replay, event-driven FVG with no look-ahead."""
from __future__ import annotations
import json, os
from pathlib import Path
from combined_gold_hunter_backtest import (
    load, agg, pt, add_fvg_if_valid, liquidity_setup,
    fvg_setup_at_current, outcome
)

def main():
    full = load()
    m15 = agg(full, 15)
    start = os.getenv("COMBINED_START_UTC")
    end = os.getenv("COMBINED_END_UTC")
    eval_start = pt(start) if start else pt(full[0]["openTime"])
    eval_end = pt(end) if end else pt(full[-1]["openTime"])

    # Warmup is preserved. Only signals whose decision candle is inside
    # the evaluation window are counted.
    active_fvgs = []
    signals = []
    i = 50

    while i < len(full):
        t = pt(full[i]["openTime"])
        if t > eval_end:
            break

        # FVG formation is processed candle-by-candle. A newly formed FVG
        # cannot trigger on the same candle; it can only trigger later.
        add_fvg_if_valid(full, m15, i, active_fvgs)

        if t < eval_start:
            i += 1
            continue

        liquidity = liquidity_setup(full, m15, i)
        fvg = fvg_setup_at_current(full, m15, i, active_fvgs)

        if liquidity and fvg:
            chosen = {**liquidity, "engine": "CONFLUENCE"}
        else:
            chosen = liquidity or fvg

        if not chosen:
            i += 1
            continue

        chosen = {
            **chosen,
            "rr": round(chosen["rr"], 2),
            "entry": round(chosen["entry"], 3),
            "sl": round(chosen["sl"], 3),
            "tp": round(chosen["tp"], 3),
        }

        result, outcome_index = outcome(chosen, full, i)
        chosen["outcome"] = result
        signals.append(chosen)

        # One active trade at a time. Outcome scanning is allowed only after
        # the signal has been generated; it is never used to create the signal.
        if result in ("TP", "SL", "AMBIGUOUS"):
            i = outcome_index + 1
        else:
            i += 1

    tp_count = sum(x["outcome"] == "TP" for x in signals)
    sl_count = sum(x["outcome"] == "SL" for x in signals)
    amb_count = sum(x["outcome"] == "AMBIGUOUS" for x in signals)
    net = sum(
        x["rr"] if x["outcome"] == "TP"
        else -1 if x["outcome"] == "SL"
        else 0
        for x in signals
    )

    by_engine = {}
    for engine in ("LIQUIDITY", "FVG", "CONFLUENCE"):
        ss = [x for x in signals if x["engine"] == engine]
        w = sum(x["outcome"] == "TP" for x in ss)
        l = sum(x["outcome"] == "SL" for x in ss)
        by_engine[engine] = {
            "signals": len(ss),
            "tp": w,
            "sl": l,
            "ambiguous": sum(x["outcome"] == "AMBIGUOUS" for x in ss),
            "win_rate": round(100 * w / (w + l), 2) if w + l else None,
            "net_r": round(sum(
                x["rr"] if x["outcome"] == "TP"
                else -1 if x["outcome"] == "SL"
                else 0
                for x in ss
            ), 2),
        }

    result = {
        "status": "COMPLETED",
        "source": os.getenv("COMBINED_SOURCE_URL"),
        "validation_start_utc": start,
        "validation_end_utc": end,
        "data": {
            "m5": len(full),
            "m15": len(m15),
            "first_m5": full[0]["openTime"],
            "last_m5": full[-1]["openTime"],
        },
        "overall": {
            "signals": len(signals),
            "tp": tp_count,
            "sl": sl_count,
            "ambiguous": amb_count,
            "win_rate": round(100 * tp_count / (tp_count + sl_count), 2)
                if tp_count + sl_count else None,
            "net_r": round(net, 2),
            "avg_rr": round(sum(x["rr"] for x in signals) / len(signals), 2)
                if signals else None,
        },
        "by_engine": by_engine,
        "signals": signals,
        "notes": [
            "Event-driven replay: FVGs are formed only from closed candles and can trigger only on later candles.",
            "Warmup data is preserved before the evaluation window; only signals inside the evaluation window are counted.",
            "No future candle is inspected when deciding whether a setup exists.",
            "One active trade at a time; outcomes are evaluated only after a signal.",
            "Fixed rules, no parameter tuning; research only.",
        ],
    }

    p = Path("backtest/combined_gold_hunter_results.json")
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
