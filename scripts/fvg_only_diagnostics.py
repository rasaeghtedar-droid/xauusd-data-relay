#!/usr/bin/env python3
"""FVG-only diagnostics. Research only; does not change trading rules."""
from __future__ import annotations
import json, os
from pathlib import Path
from scripts.fvg_only_gold_hunter_backtest import (
    load, agg, fvg_at, target, pt, PAD
)

def main():
    m5 = load()
    start = os.getenv("FVG_ONLY_START_UTC")
    end = os.getenv("FVG_ONLY_END_UTC")
    if start:
        m5 = [x for x in m5 if pt(x["openTime"]) >= pt(start)]
    if end:
        m5 = [x for x in m5 if pt(x["openTime"]) <= pt(end)]
    m15 = agg(m5, 15)

    raw = {"total": 0, "BUY": 0, "SELL": 0}
    for i in range(50, len(m5)):
        f = fvg_at(m5, i)
        if f:
            raw["total"] += 1
            raw[f["direction"]] += 1

    life = {
        "formed_pending": 0,
        "invalidated_before_mid": 0,
        "midpoint_touched": 0,
        "direction_confirmed": 0,
        "target_missing": 0,
        "signals": 0,
        "BUY_signals": 0,
        "SELL_signals": 0,
    }
    pending = None
    active_until = None
    i = 50
    while i < len(m5):
        if active_until and pt(m5[i]["openTime"]) <= active_until:
            pending = None
            i += 1
            continue

        c = m5[i]
        if pending is not None:
            f = pending
            if (f["direction"] == "BUY" and c["low"] <= f["lo"]) or (f["direction"] == "SELL" and c["high"] >= f["hi"]):
                life["invalidated_before_mid"] += 1
                pending = None
            elif c["low"] <= f["mid"] <= c["high"]:
                life["midpoint_touched"] += 1
                ok = (
                    f["direction"] == "BUY" and c["close"] > f["mid"] and c["close"] > c["open"]
                ) or (
                    f["direction"] == "SELL" and c["close"] < f["mid"] and c["close"] < c["open"]
                )
                if ok:
                    life["direction_confirmed"] += 1
                    entry = f["mid"]
                    sl = f["lo"] - PAD if f["direction"] == "BUY" else f["hi"] + PAD
                    tp = target(f["direction"], entry, sl, f["ctx"])
                    if tp is None:
                        life["target_missing"] += 1
                        pending = None
                    else:
                        life["signals"] += 1
                        life[f"{f['direction']}_signals"] += 1
                        pending = None
                        # Match baseline's one-active-setup behavior.
                        for k in range(i + 1, len(m5)):
                            b = m5[k]
                            sl_hit = b["low"] <= sl if f["direction"] == "BUY" else b["high"] >= sl
                            tp_hit = b["high"] >= tp if f["direction"] == "BUY" else b["low"] <= tp
                            if sl_hit or tp_hit:
                                active_until = pt(b["openTime"])
                                break

        if pending is None:
            f = fvg_at(m5, i)
            if f:
                t = pt(f["time"])
                ctx = [x for x in m15 if pt(x["openTime"]) <= t - __import__("datetime").timedelta(minutes=15)][-30:]
                pending = {**f, "ctx": ctx, "formed_index": i}
                life["formed_pending"] += 1
        i += 1

    result = {
        "status": "COMPLETED",
        "validation_start_utc": start,
        "validation_end_utc": end,
        "raw_fvg_formations": raw,
        "stateful_lifecycle": life,
        "notes": [
            "Diagnostic only; baseline strategy rules are unchanged.",
            "Counts use the same FVG detector and one-active-setup lifecycle as the FVG-only baseline.",
        ],
    }
    Path("backtest").mkdir(exist_ok=True)
    Path("backtest/fvg_only_diagnostics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
