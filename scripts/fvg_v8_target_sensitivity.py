#!/usr/bin/env python3
"""Research-only V8 target sensitivity replay.

Purpose:
- Keep the locked V8 FVG detection, confirmation, SL, eligibility and one-active-trade lifecycle.
- Change ONLY the TP model for counterfactual target sensitivity.
- Structural baseline uses the locked structural target.
- RR_CAP_* models cap the realized target RR at the selected cap when the structural
  target is farther away. They do NOT admit setups whose structural target RR < 2.
- No production/V8 rules are changed by this script.
"""
from __future__ import annotations
import os, json, bisect
from pathlib import Path
from datetime import timedelta
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fvg_only_gold_hunter_backtest import load, agg, fvg_at, target, pt, PAD, LOOKBACK

START = os.getenv("FVG_ONLY_START_UTC")
END = os.getenv("FVG_ONLY_END_UTC")
SOURCE = os.getenv("COMBINED_SOURCE_URL")

CAPS = (2.0, 3.0, 4.0, 5.0)

def run_model(m5, m15, model):
    m15_times = [pt(x["openTime"]) for x in m15]
    pending = {}
    mid_index = {"BUY": [], "SELL": []}
    invalid_index = {"BUY": [], "SELL": []}
    next_id = 0
    active = None
    trades = []
    confirmations = 0
    missed = 0
    invalidated = 0
    i = 50

    while i < len(m5):
        c = m5[i]
        had_active = active is not None

        # Resolve only the current closed candle. No same-candle exit.
        if active is not None:
            sl_hit = c["low"] <= active["sl"] if active["direction"] == "BUY" else c["high"] >= active["sl"]
            tp_hit = c["high"] >= active["tp"] if active["direction"] == "BUY" else c["low"] <= active["tp"]
            if sl_hit and tp_hit:
                active["outcome"] = "AMBIGUOUS"
                active["exit_time"] = c["openTime"]
                trades.append(active)
                active = None
            elif sl_hit:
                active["outcome"] = "SL"
                active["exit_time"] = c["openTime"]
                trades.append(active)
                active = None
            elif tp_hit:
                active["outcome"] = "TP"
                active["exit_time"] = c["openTime"]
                trades.append(active)
                active = None

        confirmed = []

        # Invalidate before confirmation.
        for d in ("BUY", "SELL"):
            arr = invalid_index[d]
            if d == "BUY":
                cut = bisect.bisect_right(arr, (-c["low"], 10**18))
            else:
                cut = bisect.bisect_right(arr, (c["high"], 10**18))
            for _, fid in arr[:cut]:
                if pending.pop(fid, None) is not None:
                    invalidated += 1
            invalid_index[d] = arr[cut:]

        # Confirm pending FVGs touched by this candle.
        for d in ("BUY", "SELL"):
            arr = mid_index[d]
            lo = bisect.bisect_left(arr, (c["low"], -1))
            hi = bisect.bisect_right(arr, (c["high"], 10**18))
            for _, fid in arr[lo:hi]:
                f = pending.get(fid)
                if f is None:
                    continue
                if (d == "BUY" and c["low"] <= f["lo"]) or (d == "SELL" and c["high"] >= f["hi"]):
                    pending.pop(fid, None)
                    invalidated += 1
                    continue

                ok = (
                    (d == "BUY" and c["close"] > f["mid"] and c["close"] > c["open"]) or
                    (d == "SELL" and c["close"] < f["mid"] and c["close"] < c["open"])
                )
                if not ok:
                    continue

                confirmations += 1
                entry = f["mid"]
                sl = f["lo"] - PAD if d == "BUY" else f["hi"] + PAD

                # IMPORTANT: eligibility remains locked to the V8 structural target.
                structural_tp = target(d, entry, sl, f["ctx"])
                pending.pop(fid, None)
                if structural_tp is None:
                    continue

                structural_rr = (
                    (structural_tp - entry) / (entry - sl)
                    if d == "BUY"
                    else (entry - structural_tp) / (sl - entry)
                )
                if structural_rr < 2.0:
                    continue

                if model == "STRUCTURAL":
                    tp = structural_tp
                    realized_rr = structural_rr
                else:
                    cap = float(model.split("_")[-1])
                    risk = entry - sl if d == "BUY" else sl - entry
                    capped_rr = min(structural_rr, cap)
                    tp = entry + risk * capped_rr if d == "BUY" else entry - risk * capped_rr
                    realized_rr = capped_rr

                confirmed.append({
                    "engine": f"V8_TARGET_SENSITIVITY_{model}",
                    "direction": d,
                    "formation_time": f["time"],
                    "confirmation_time": c["openTime"],
                    "entry": round(entry, 3),
                    "sl": round(sl, 3),
                    "tp": round(tp, 3),
                    "rr": round(realized_rr, 2),
                    "structural_rr": round(structural_rr, 2),
                    "confirmation_index": i,
                    "entry_index": i,
                    "_raw_entry": entry,
                    "_raw_sl": sl,
                    "_raw_tp": tp,
                    "zone_lo": f["lo"],
                    "zone_hi": f["hi"],
                })

        # Confirmations while an earlier trade was active are missed.
        if had_active:
            missed += len(confirmed)
        elif active is None and confirmed:
            confirmed.sort(key=lambda x: (x["formation_time"], x["confirmation_time"]))
            active = confirmed[0]
            missed += max(0, len(confirmed) - 1)

        # Detect new FVG only after current-candle processing.
        f = fvg_at(m5, i)
        if f:
            t = pt(f["time"])
            cutoff = t - timedelta(minutes=15)
            mi = bisect.bisect_right(m15_times, cutoff)
            ctx = m15[max(0, mi - LOOKBACK):mi]
            fid = next_id
            next_id += 1
            pending[fid] = {**f, "ctx": ctx, "formed_index": i}
            bisect.insort(mid_index[f["direction"]], (f["mid"], fid))
            if f["direction"] == "BUY":
                bisect.insort(invalid_index["BUY"], (-f["lo"], fid))
            else:
                bisect.insort(invalid_index["SELL"], (f["hi"], fid))

        i += 1

    if active is not None:
        active["outcome"] = "OPEN_AT_DATA_END"
        active["exit_time"] = None
        trades.append(active)

    tp_n = sum(x["outcome"] == "TP" for x in trades)
    sl_n = sum(x["outcome"] == "SL" for x in trades)
    amb_n = sum(x["outcome"] == "AMBIGUOUS" for x in trades)
    open_n = sum(x["outcome"] == "OPEN_AT_DATA_END" for x in trades)
    net = sum(
        x["rr"] if x["outcome"] == "TP"
        else -1 if x["outcome"] == "SL"
        else 0
        for x in trades
    )

    # Trade-path equity drawdown in R, using actual closed-trade sequence.
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in trades:
        if x["outcome"] == "TP":
            equity += x["rr"]
        elif x["outcome"] == "SL":
            equity -= 1.0
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "model": model,
        "signals": len(trades),
        "tp": tp_n,
        "sl": sl_n,
        "ambiguous": amb_n,
        "open_at_data_end": open_n,
        "win_rate": round(100 * tp_n / (tp_n + sl_n), 2) if tp_n + sl_n else None,
        "net_r": round(net, 2),
        "conservative_net_r": round(net - amb_n, 2),
        "avg_realized_rr": round(sum(x["rr"] for x in trades) / len(trades), 2) if trades else None,
        "max_trade_path_dd_r": round(max_dd, 2),
        "confirmations": confirmations,
        "missed_or_competing": missed,
        "invalidated_pending_fvgs": invalidated,
        "remaining_pending_fvgs": len(pending),
        "trades": trades,
    }

def main():
    if not SOURCE:
        raise RuntimeError("COMBINED_SOURCE_URL is required")
    m5 = load()
    if START:
        m5 = [x for x in m5 if pt(x["openTime"]) >= pt(START)]
    if END:
        m5 = [x for x in m5 if pt(x["openTime"]) <= pt(END)]
    m15 = agg(m5, 15)

    models = ["STRUCTURAL"] + [f"RR_CAP_{int(c)}" for c in CAPS]
    results = [run_model(m5, m15, model) for model in models]

    baseline = next(x for x in results if x["model"] == "STRUCTURAL")
    result = {
        "status": "COMPLETED",
        "research_only": True,
        "purpose": "Isolate whether V8 performance is driven primarily by unusually large structural targets.",
        "source": SOURCE,
        "validation_start_utc": START,
        "validation_end_utc": END,
        "locked_signal_eligibility": {
            "structural_target_required": True,
            "minimum_structural_rr": 2.0,
            "fvg_detection_unchanged": True,
            "confirmation_unchanged": True,
            "sl_unchanged": True,
            "one_active_trade": True,
            "no_queue": True,
            "no_lookahead": True,
        },
        "baseline_guard": {
            "expected_v8_signals": 130,
            "structural_model_signals": baseline["signals"],
            "matches_locked_v8_count": baseline["signals"] == 130,
        },
        "models": results,
        "interpretation_notes": [
            "STRUCTURAL reproduces the locked V8 target model.",
            "RR_CAP_2/3/4/5 keep the same structural eligibility and FVG/SL rules, but cap the realized target RR.",
            "These are counterfactual research models; no V8 production rule is modified.",
            "A materially different result across caps indicates that target distance/lifecycle contributes materially to the headline V8 performance.",
        ],
    }

    p = Path("backtest/fvg_v8_target_sensitivity_results.json")
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
