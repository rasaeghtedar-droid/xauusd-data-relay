#!/usr/bin/env python3
"""Temporal FVG Entry Audit v2.

Compares two explicit entry-timing models on the same confirmed FVG events:
A) confirmation-candle midpoint entry;
B) midpoint resting order that can fill only on a strictly later closed candle.

One active trade at a time, no queue, no lookahead. Targets use only M15
context available before FVG formation.
"""
from __future__ import annotations
import os, json
from pathlib import Path
from datetime import timedelta
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fvg_only_gold_hunter_backtest import load, agg, fvg_at, target, PAD, LOOKBACK, pt

START = os.getenv("FVG_ONLY_START_UTC")
END = os.getenv("FVG_ONLY_END_UTC")
SOURCE = os.getenv("COMBINED_SOURCE_URL")

def result_template(mode):
    return {
        "mode": mode, "trades": [], "confirmations": 0,
        "missed_or_competing_opportunities": 0,
        "invalidated_pending_fvgs": 0,
        "remaining_pending_fvgs": 0,
        "resting_order_at_end": False,
    }

def finalize(r):
    ts = r["trades"]
    tp = sum(x["outcome"] == "TP" for x in ts)
    sl = sum(x["outcome"] == "SL" for x in ts)
    amb = sum(x["outcome"] == "AMBIGUOUS" for x in ts)
    op = sum(x["outcome"] == "OPEN_AT_DATA_END" for x in ts)
    net = sum(x["rr"] if x["outcome"] == "TP" else -1 if x["outcome"] == "SL" else 0 for x in ts)
    by = {}
    for d in ("BUY", "SELL"):
        ss = [x for x in ts if x["direction"] == d]
        w = sum(x["outcome"] == "TP" for x in ss)
        l = sum(x["outcome"] == "SL" for x in ss)
        by[d] = {
            "signals": len(ss), "tp": w, "sl": l,
            "ambiguous": sum(x["outcome"] == "AMBIGUOUS" for x in ss),
            "open_at_data_end": sum(x["outcome"] == "OPEN_AT_DATA_END" for x in ss),
            "win_rate": round(100*w/(w+l), 2) if w+l else None,
            "net_r": round(sum(x["rr"] if x["outcome"] == "TP" else -1 if x["outcome"] == "SL" else 0 for x in ss), 2),
        }
    r["overall"] = {
        "signals": len(ts), "tp": tp, "sl": sl, "ambiguous": amb,
        "open_at_data_end": op,
        "win_rate": round(100*tp/(tp+sl), 2) if tp+sl else None,
        "net_r": round(net, 2),
        "conservative_net_r": round(net-amb, 2),
        "avg_rr": round(sum(x["rr"] for x in ts)/len(ts), 2) if ts else None,
    }
    r["by_direction"] = by
    return r

def exit_after_fill(trade, m5, fill_index):
    for k in range(fill_index + 1, len(m5)):
        b = m5[k]
        sl = b["low"] <= trade["sl"] if trade["direction"] == "BUY" else b["high"] >= trade["sl"]
        tp = b["high"] >= trade["tp"] if trade["direction"] == "BUY" else b["low"] <= trade["tp"]
        if sl and tp:
            return "AMBIGUOUS", b["openTime"]
        if tp:
            return "TP", b["openTime"]
        if sl:
            return "SL", b["openTime"]
    return "OPEN_AT_DATA_END", None

def main():
    m5 = load()
    if START:
        m5 = [x for x in m5 if pt(x["openTime"]) >= pt(START)]
    if END:
        m5 = [x for x in m5 if pt(x["openTime"]) <= pt(END)]
    m15 = agg(m5, 15)

    A = result_template("A_CONFIRMATION_CANDLE")
    B = result_template("B_STRICTLY_LATER_FILL")
    pending = []
    resting_B = None
    active_A = None
    active_B = None
    i = 50

    while i < len(m5):
        c = m5[i]

        if active_A is not None:
            out, ex = exit_after_fill(active_A, m5, i)
            active_A["outcome"] = out
            active_A["exit_time"] = ex
            A["trades"].append(active_A)
            active_A = None

        if active_B is not None:
            out, ex = exit_after_fill(active_B, m5, i)
            active_B["outcome"] = out
            active_B["exit_time"] = ex
            B["trades"].append(active_B)
            active_B = None

        if resting_B is not None:
            invalid = (resting_B["direction"] == "BUY" and c["low"] <= resting_B["lo"]) or (resting_B["direction"] == "SELL" and c["high"] >= resting_B["hi"])
            if invalid:
                resting_B = None
            elif i > resting_B["confirmation_index"] and c["low"] <= resting_B["mid"] <= c["high"]:
                t = {**resting_B, "entry_index": i, "entry_time": c["openTime"]}
                active_B = {
                    "engine": "FVG_TEMPORAL_AUDIT_V2_B",
                    "direction": t["direction"], "formation_time": t["formation_time"],
                    "confirmation_time": t["confirmation_time"],
                    "entry": round(t["mid"], 3), "sl": round(t["sl"], 3),
                    "tp": round(t["tp"], 3), "rr": round(t["rr"], 2),
                    "confirmation_index": t["confirmation_index"],
                    "entry_index": i, "entry_time": c["openTime"],
                    "zone_lo": t["lo"], "zone_hi": t["hi"],
                }
                resting_B = None

        confirmed = []
        still = []
        for f in pending:
            invalid = (f["direction"] == "BUY" and c["low"] <= f["lo"]) or (f["direction"] == "SELL" and c["high"] >= f["hi"])
            if invalid:
                A["invalidated_pending_fvgs"] += 1
                B["invalidated_pending_fvgs"] += 1
                continue
            if c["low"] <= f["mid"] <= c["high"]:
                ok = (f["direction"] == "BUY" and c["close"] > f["mid"] and c["close"] > c["open"]) or (f["direction"] == "SELL" and c["close"] < f["mid"] and c["close"] < c["open"])
                if ok:
                    A["confirmations"] += 1
                    B["confirmations"] += 1
                    sl = f["lo"] - PAD if f["direction"] == "BUY" else f["hi"] + PAD
                    tp = target(f["direction"], f["mid"], sl, f["ctx"])
                    if tp is not None:
                        rr = (tp-f["mid"])/(f["mid"]-sl) if f["direction"] == "BUY" else (f["mid"]-tp)/(sl-f["mid"])
                        confirmed.append({**f, "confirmation_index": i, "confirmation_time": c["openTime"], "sl": sl, "tp": tp, "rr": rr})
                    continue
            still.append(f)
        pending = still

        if confirmed:
            confirmed.sort(key=lambda x: (x["confirmation_index"], x["formed_index"]))
            chosen = confirmed[0]
            if active_A is None:
                active_A = {
                    "engine": "FVG_TEMPORAL_AUDIT_V2_A",
                    "direction": chosen["direction"], "formation_time": chosen["time"],
                    "confirmation_time": chosen["confirmation_time"],
                    "entry": round(chosen["mid"], 3), "sl": round(chosen["sl"], 3),
                    "tp": round(chosen["tp"], 3), "rr": round(chosen["rr"], 2),
                    "confirmation_index": i, "entry_index": i,
                    "entry_time": c["openTime"], "zone_lo": chosen["lo"], "zone_hi": chosen["hi"],
                }
            else:
                A["missed_or_competing_opportunities"] += 1

            if active_B is None and resting_B is None:
                resting_B = chosen
            else:
                B["missed_or_competing_opportunities"] += 1

        f = fvg_at(m5, i)
        if f:
            t = pt(f["time"])
            ctx = [x for x in m15 if pt(x["openTime"]) <= t - timedelta(minutes=15)][-LOOKBACK:]
            pending.append({**f, "ctx": ctx, "formed_index": i, "formation_time": f["time"]})
        i += 1

    if active_A is not None:
        active_A["outcome"] = "OPEN_AT_DATA_END"; active_A["exit_time"] = None; A["trades"].append(active_A)
    if active_B is not None:
        active_B["outcome"] = "OPEN_AT_DATA_END"; active_B["exit_time"] = None; B["trades"].append(active_B)

    A["remaining_pending_fvgs"] = len(pending)
    B["remaining_pending_fvgs"] = len(pending)
    B["resting_order_at_end"] = resting_B is not None
    A = finalize(A)
    B = finalize(B)

    result = {
        "status": "COMPLETED",
        "research_only": True,
        "source": SOURCE,
        "validation_start_utc": START,
        "validation_end_utc": END,
        "data": {
            "m5": len(m5), "m15": len(m15),
            "first_m5": m5[0]["openTime"], "last_m5": m5[-1]["openTime"],
        },
        "audit_rules": {
            "confirmation_definition": "FVG midpoint return + directional confirmation",
            "target_context": "M15 candles strictly before FVG formation",
            "one_active_trade": True, "queue": False, "lookahead": False,
            "A": "entry at midpoint on confirmation candle; exits evaluated only from next closed candle",
            "B": "midpoint order can fill only on a strictly later closed candle; exits evaluated only from fill+1",
        },
        "A_confirmation_candle": A,
        "B_strictly_later_fill": B,
        "comparison": {
            "confirmation_count": A["confirmations"],
            "A_signals": A["overall"]["signals"],
            "B_signals": B["overall"]["signals"],
            "A_net_r": A["overall"]["net_r"],
            "B_net_r": B["overall"]["net_r"],
        },
    }
    p = Path("backtest/fvg_temporal_audit_v2_results.json")
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    if not SOURCE:
        raise RuntimeError("COMBINED_SOURCE_URL is required for temporal audit v2")
    main()
