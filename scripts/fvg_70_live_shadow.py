#!/usr/bin/env python3
"""Isolated live shadow test: V8 Cap4 with 70% FVG depth and hypothetical 0.42 spread.
Never sends orders, never writes the production dashboard payload, and never sends Telegram.
"""
from __future__ import annotations
import json
from pathlib import Path
import fvg_v8_live_dry_run as live

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "fvg_70_live_shadow.json"
live.LIVE_PATH = OUT
live.ANALYSIS_PATH = ROOT / "data" / "_fvg_70_shadow_analysis.json"
live.HISTORY_PATH = ROOT / "data" / "_fvg_70_shadow_history.json"
live.ACTIVE_SIGNAL_PATH = ROOT / "data" / "_fvg_70_shadow_active.json"
live.merge_into_analysis = lambda payload: None

ORIGINAL_FVG_AT = live.fvg_at
DEPTH = 0.70
ASSUMED_SPREAD = 0.42

def fvg_at_70(candles, i):
    fvg = ORIGINAL_FVG_AT(candles, i)
    if not fvg:
        return fvg
    adjusted = dict(fvg)
    width = float(adjusted["hi"]) - float(adjusted["lo"])
    if width <= 0:
        return None
    adjusted["mid"] = (
        float(adjusted["hi"]) - DEPTH * width
        if adjusted["direction"] == "BUY"
        else float(adjusted["lo"]) + DEPTH * width
    )
    return adjusted

live.fvg_at = fvg_at_70
live.main()

payload = json.loads(OUT.read_text(encoding="utf-8"))
payload["test_mode"] = "ISOLATED_LIVE_SHADOW"
payload["entry_model"] = "FVG_70_PERCENT_DEPTH"
payload["assumed_spread_price_units"] = ASSUMED_SPREAD
payload["real_orders_sent"] = False
payload["telegram_sent"] = False
payload["production_dashboard_modified"] = False
signal = payload.get("signal")
if signal:
    entry = float(signal["entry"])
    sl = float(signal["sl"])
    tp = float(signal["tp"])
    direction = signal["direction"]
    risk = entry - sl if direction == "BUY" else sl - entry
    reward = tp - entry if direction == "BUY" else entry - tp
    cost_r = ASSUMED_SPREAD / risk if risk > 0 else None
    payload["spread_simulation"] = {
        "status": "ESTIMATE_ONLY_OHLC_HAS_NO_BID_ASK",
        "assumption": "0.42 price units total round-trip spread cost; not broker-verified for this candle",
        "risk_price_units": round(risk, 5),
        "gross_reward_price_units": round(reward, 5),
        "gross_rr": round(reward / risk, 4) if risk > 0 else None,
        "spread_cost_r": round(cost_r, 4) if cost_r is not None else None,
        "estimated_net_rr": round(reward / risk - cost_r, 4) if risk > 0 and cost_r is not None else None,
        "entry_fill_confirmed": False,
        "reason": "Biquote OHLC candles do not provide tick-level Bid/Ask; this estimates spread impact but cannot confirm a fill."
    }
else:
    payload["spread_simulation"] = {
        "status": "NO_CURRENT_SIGNAL",
        "assumed_spread_price_units": ASSUMED_SPREAD,
        "note": "Spread-adjusted RR is computed when a 70% signal exists."
    }
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({
    "status": payload.get("status"),
    "engine": payload.get("engine"),
    "entry_model": payload.get("entry_model"),
    "latest_closed_m5": payload.get("latest_closed_m5"),
    "signal": payload.get("signal"),
    "spread_simulation": payload.get("spread_simulation"),
    "real_orders_sent": False
}, ensure_ascii=False, indent=2))
