#!/usr/bin/env python3
"""Isolated live shadow test: V8 Cap4 with 70% FVG depth and hypothetical 0.42 spread.
Never sends orders, never writes the production dashboard payload, and never sends Telegram.
"""
from __future__ import annotations
import json
from datetime import timedelta
from pathlib import Path
import fvg_v8_live_dry_run as live

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "fvg_70_live_shadow.json"
HISTORY_OUT = ROOT / "data" / "fvg_70_live_shadow_history.json"
live.LIVE_PATH = OUT
live.ANALYSIS_PATH = ROOT / "data" / "_fvg_70_shadow_analysis.json"
live.HISTORY_PATH = ROOT / "data" / "_fvg_70_shadow_history.json"
live.ACTIVE_SIGNAL_PATH = ROOT / "data" / "_fvg_70_shadow_active.json"
live.merge_into_analysis = lambda payload: None

ORIGINAL_FVG_AT = live.fvg_at
ORIGINAL_RUN_V8 = live.run_v8
DEPTH = 0.70
ASSUMED_SPREAD = 0.42
PENDING_MAX_MINUTES = 60
CAPTURE = {}

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

def capture_run_v8(m5):
    result = ORIGINAL_RUN_V8(m5)
    CAPTURE["replay_history"] = result[4] if len(result) > 4 else []
    CAPTURE["replay_meta"] = result[2] if len(result) > 2 else {}
    return result

live.fvg_at = fvg_at_70
live.run_v8 = capture_run_v8
live.main()

payload = json.loads(OUT.read_text(encoding="utf-8"))
payload["test_mode"] = "ISOLATED_LIVE_SHADOW"
payload["entry_model"] = "FVG_70_PERCENT_DEPTH"
payload["fvg_entry_depth_fraction"] = DEPTH
payload["assumed_spread_price_units"] = ASSUMED_SPREAD
payload["real_orders_sent"] = False
payload["telegram_sent"] = False
payload["production_dashboard_modified"] = False
payload["shadow_replay_meta"] = CAPTURE.get("replay_meta", {})
payload["spread_model_note"] = (
    "0.42 price units treated as estimated round-trip spread cost for entry-activated trades only. "
    "OHLC bars cannot verify Bid/Ask fills; this is not a broker-execution simulation."
)

history = []
for original in CAPTURE.get("replay_history", []):
    item = dict(original)
    status = str(item.get("status", ""))
    entry_activated = bool(item.get("activation_time"))
    # Expiry is a time-based cancellation, not an executed trade. If the next
    # available bar is after a market closure/gap, record the actual deadline.
    if status == "EXPIRED":
        confirmation = item.get("confirmation_time")
        if confirmation:
            try:
                confirmed_at = live.pt(confirmation)
                item["expiry_deadline"] = (confirmed_at + timedelta(minutes=PENDING_MAX_MINUTES)).isoformat().replace("+00:00", "Z")
                item["exit_time"] = item["expiry_deadline"]
            except (TypeError, ValueError):
                pass
        item["spread_cost_r_estimate"] = 0.0
        item["estimated_net_r_after_spread"] = 0.0
        item["spread_charged"] = False
    else:
        entry = item.get("entry")
        sl = item.get("sl")
        direction = item.get("direction")
        try:
            risk = float(entry) - float(sl) if direction == "BUY" else float(sl) - float(entry)
        except (TypeError, ValueError):
            risk = 0.0
        # Only charge spread if Entry was actually touched. Non-activated
        # pending/invalidation records are not fills.
        spread_r = ASSUMED_SPREAD / risk if risk > 0 and entry_activated else 0.0
        item["spread_cost_r_estimate"] = round(spread_r, 4) if entry_activated else 0.0
        result_r = item.get("result_r")
        item["estimated_net_r_after_spread"] = (
            round(float(result_r) - spread_r, 4)
            if result_r is not None and entry_activated
            else (0.0 if status in ("INVALIDATED", "EXPIRED") else None)
        )
        item["spread_charged"] = entry_activated
    item["shadow_entry_model"] = "FVG_70_PERCENT_DEPTH"
    item["assumed_spread_price_units"] = ASSUMED_SPREAD
    item["execution_verified"] = False
    history.append(item)

resolved = [x for x in history if x.get("status") in ("TP", "SL", "AMBIGUOUS")]
tp_count = sum(x.get("status") == "TP" for x in resolved)
sl_count = sum(x.get("status") == "SL" for x in resolved)
amb_count = sum(x.get("status") == "AMBIGUOUS" for x in resolved)
gross_r = sum(float(x.get("result_r") or 0) for x in resolved)
net_r = sum(
    float(x["estimated_net_r_after_spread"])
    for x in resolved if x.get("estimated_net_r_after_spread") is not None
)
payload["shadow_history_stats"] = {
    "replay_records": len(history),
    "resolved_executed_or_activated": len(resolved),
    "tp": tp_count,
    "sl": sl_count,
    "ambiguous": amb_count,
    "expired_unfilled": sum(x.get("status") == "EXPIRED" for x in history),
    "invalidated_unfilled": sum(x.get("status") == "INVALIDATED" for x in history),
    "pending_or_active": sum(x.get("status") in ("PENDING", "ACTIVE") for x in history),
    "win_rate_percent_excluding_ambiguous": round(tp_count / (tp_count + sl_count) * 100, 2)
        if tp_count + sl_count else None,
    "gross_result_r": round(gross_r, 4),
    "estimated_net_result_r_after_spread": round(net_r, 4),
    "spread_charged_only_to_entry_activated": True,
    "spread_assumption": ASSUMED_SPREAD,
    "warning": "Rolling-window OHLC replay estimate, not tick-accurate fills; history only covers candles fetched in this run."
}
payload["shadow_history_file"] = HISTORY_OUT.name
HISTORY_OUT.write_text(json.dumps({
    "test_mode": "ISOLATED_LIVE_SHADOW",
    "entry_model": "FVG_70_PERCENT_DEPTH",
    "assumed_spread_price_units": ASSUMED_SPREAD,
    "execution_verified": False,
    "records": history,
    "stats": payload["shadow_history_stats"]
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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
        "risk_price_units": round(risk, 5),
        "gross_reward_price_units": round(reward, 5),
        "gross_rr": round(reward / risk, 4) if risk > 0 else None,
        "spread_cost_r": round(cost_r, 4) if cost_r is not None else None,
        "estimated_net_rr": round(reward / risk - cost_r, 4) if risk > 0 and cost_r is not None else None,
        "entry_fill_confirmed": False
    }
else:
    payload["spread_simulation"] = {
        "status": "NO_CURRENT_SIGNAL",
        "assumed_spread_price_units": ASSUMED_SPREAD,
        "note": "No current signal; only entry-activated replay trades are charged spread in shadow_history_stats."
    }

OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({
    "status": payload.get("status"),
    "entry_model": payload.get("entry_model"),
    "latest_closed_m5": payload.get("latest_closed_m5"),
    "signal": payload.get("signal"),
    "shadow_history_stats": payload.get("shadow_history_stats"),
    "spread_simulation": payload.get("spread_simulation"),
    "real_orders_sent": False,
    "telegram_sent": False,
    "production_dashboard_modified": False
}, ensure_ascii=False, indent=2))
