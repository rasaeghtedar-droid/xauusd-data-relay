#!/usr/bin/env python3
"""Research-only V8 Cap4 FVG entry-depth comparison with raw-price PnL audit."""
from __future__ import annotations

import json
import os
from pathlib import Path

import scripts.fvg_executable_audit_v8 as v8

RESULT = Path("backtest/fvg_executable_audit_v8_results.json")
OUT = Path("backtest/fvg_90_depth_comparison.json")
AUDIT_OUT = Path("backtest/fvg_raw_price_audit.json")
ORIGINAL_FVG_AT = v8.fvg_at
COST_R_SCENARIOS = tuple(round(i / 100, 2) for i in range(0, 31))


def run_model(label: str, depth: float | None) -> dict:
    RESULT.unlink(missing_ok=True)
    v8.fvg_at = ORIGINAL_FVG_AT

    if depth is not None:
        def fvg_at_depth(candles, i):
            fvg = ORIGINAL_FVG_AT(candles, i)
            if not fvg:
                return fvg
            adjusted = dict(fvg)
            width = adjusted["hi"] - adjusted["lo"]
            if width <= 0:
                return None
            adjusted["mid"] = (
                adjusted["hi"] - depth * width
                if adjusted["direction"] == "BUY"
                else adjusted["lo"] + depth * width
            )
            return adjusted
        v8.fvg_at = fvg_at_depth

    v8.main()
    if not RESULT.exists():
        raise RuntimeError(f"{label}: no fresh result file")
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    if result.get("status") != "COMPLETED":
        raise RuntimeError(f"{label}: status={result.get('status')}")
    invariants = result.get("invariants", {})
    if invariants.get("status") != "PASS" or invariants.get("errors"):
        raise RuntimeError(f"{label}: invariant failure: {invariants}")

    trades = result.get("trades", [])
    mismatches = []
    for index, trade in enumerate(trades):
        lo, hi, entry = trade.get("zone_lo"), trade.get("zone_hi"), trade.get("entry")
        direction = trade.get("direction")
        if None in (lo, hi, entry) or hi <= lo:
            mismatches.append(f"TRADE_ZONE_MISSING:{index}")
            continue
        width = hi - lo
        expected = (lo + hi) / 2 if depth is None else (
            hi - depth * width if direction == "BUY" else lo + depth * width
        )
        if abs(float(entry) - round(expected, 3)) > 0.001:
            mismatches.append(f"ENTRY_LEVEL_MISMATCH:{index}")
    if mismatches:
        raise RuntimeError(f"{label}: entry validation failed: {mismatches[:10]}")

    result["_entry_validation"] = {
        "status": "PASS", "trades_checked": len(trades),
        "entry_definition": "midpoint" if depth is None else f"{int(depth * 100)}%-depth",
    }
    return result


def raw_trade_r(trade: dict) -> float:
    """Recompute reward/risk from unrounded execution prices, not display RR."""
    direction = trade["direction"]
    entry = float(trade.get("_raw_entry", trade["entry"]))
    sl = float(trade.get("_raw_sl", trade["sl"]))
    tp = float(trade.get("_raw_tp", trade["tp"]))
    risk = entry - sl if direction == "BUY" else sl - entry
    reward = tp - entry if direction == "BUY" else entry - tp
    if risk <= 0:
        raise RuntimeError(f"Non-positive risk in trade {trade.get('entry_time')}")
    return reward / risk


def audit_trades(result: dict, label: str) -> dict:
    rows = []
    errors = []
    for index, trade in enumerate(result.get("trades", [])):
        outcome = trade.get("outcome")
        rr_raw = raw_trade_r(trade)
        rr_display = float(trade.get("rr", 0))
        if abs(round(rr_raw, 2) - rr_display) > 0.011:
            errors.append(f"RR_DISPLAY_MISMATCH:{label}:{index}")
        if rr_raw < 2.0 - 1e-9:
            errors.append(f"RAW_RR_BELOW_2:{label}:{index}")
        if outcome == "TP":
            pnl_r = rr_raw
        elif outcome == "SL":
            pnl_r = -1.0
        else:
            pnl_r = 0.0
        rows.append({
            "index": index, "direction": trade["direction"],
            "entry_time": trade.get("entry_time"), "exit_time": trade.get("exit_time"),
            "outcome": outcome, "entry_display": trade.get("entry"),
            "entry_raw": trade.get("_raw_entry"), "sl_raw": trade.get("_raw_sl"),
            "tp_raw": trade.get("_raw_tp"), "rr_display": rr_display,
            "rr_raw": round(rr_raw, 8), "pnl_r_raw": round(pnl_r, 8),
            "zone_lo": trade.get("zone_lo"), "zone_hi": trade.get("zone_hi"),
        })
    raw_net = sum(row["pnl_r_raw"] for row in rows)
    ambiguous = sum(row["outcome"] == "AMBIGUOUS" for row in rows)
    open_count = sum(row["outcome"] == "OPEN_AT_DATA_END" for row in rows)
    return {
        "label": label, "trade_count": len(rows), "tp": sum(x["outcome"] == "TP" for x in rows),
        "sl": sum(x["outcome"] == "SL" for x in rows), "ambiguous": ambiguous,
        "open_at_data_end": open_count, "raw_net_r": round(raw_net, 6),
        "conservative_raw_net_r": round(raw_net - ambiguous, 6),
        "rounded_rr_net_r": round(sum(
            float(t.get("rr", 0)) if t.get("outcome") == "TP"
            else -1.0 if t.get("outcome") == "SL" else 0.0
            for t in result.get("trades", [])
        ), 6),
        "raw_vs_rounded_delta_r": round(raw_net - sum(
            float(t.get("rr", 0)) if t.get("outcome") == "TP"
            else -1.0 if t.get("outcome") == "SL" else 0.0
            for t in result.get("trades", [])
        ), 8),
        "errors": errors, "trades": rows,
    }


def main() -> None:
    variants = [("midpoint", None), ("depth_70", 0.70), ("depth_80", 0.80), ("depth_90", 0.90)]
    results = {label: run_model(label.upper(), depth) for label, depth in variants}
    audits = {label: audit_trades(results[label], label) for label, _ in variants}
    all_errors = [e for audit in audits.values() for e in audit["errors"]]
    if all_errors:
        raise RuntimeError(f"Raw-price audit failed: {all_errors[:20]}")

    # Cost sensitivity is recomputed from exact raw-price R, using the number of trades.
    summaries = {}
    for label, _ in variants:
        result = results[label]
        audit = audits[label]
        n = audit["trade_count"]
        raw_net = audit["raw_net_r"]
        sensitivity = {}
        for cost_r in COST_R_SCENARIOS:
            total_cost = n * cost_r
            sensitivity[f"{cost_r:.2f}R_per_trade"] = {
                "assumed_round_trip_cost_r_per_trade": cost_r,
                "total_cost_r": round(total_cost, 4),
                "net_r_after_cost": round(raw_net - total_cost, 4),
                "conservative_net_r_after_cost": round(audit["conservative_raw_net_r"] - total_cost, 4),
            }
        overall = result["overall"]
        summaries[label] = {
            "signals": overall["signals"], "tp": overall["tp"], "sl": overall["sl"],
            "ambiguous": overall["ambiguous"], "open_at_data_end": overall.get("open_at_data_end", 0),
            "win_rate": overall["win_rate"], "net_r_engine_rounded": overall["net_r"],
            "net_r_raw_prices": audit["raw_net_r"],
            "conservative_net_r_raw_prices": audit["conservative_raw_net_r"],
            "raw_vs_rounded_delta_r": audit["raw_vs_rounded_delta_r"],
            "avg_rr_raw_tp": round(
                sum(raw_trade_r(t) for t in result["trades"] if t["outcome"] == "TP")
                / max(1, sum(t["outcome"] == "TP" for t in result["trades"])), 6),
            "cost_sensitivity_raw": sensitivity,
            "invariants": result["invariants"], "entry_validation": result["_entry_validation"],
        }

    baseline = summaries["midpoint"]
    deltas = {}
    for label in ("depth_70", "depth_80", "depth_90"):
        current = summaries[label]
        deltas[label] = {
            "signals_delta": current["signals"] - baseline["signals"],
            "win_rate_percentage_points_delta": round((current["win_rate"] or 0) - (baseline["win_rate"] or 0), 2),
            "raw_net_r_delta": round(current["net_r_raw_prices"] - baseline["net_r_raw_prices"], 6),
            "conservative_raw_net_r_delta": round(current["conservative_net_r_raw_prices"] - baseline["conservative_net_r_raw_prices"], 6),
        }

    audit_payload = {
        "status": "PASS" if not all_errors else "FAIL",
        "research_only": True,
        "method": "Recompute TP reward/risk from unrounded raw entry, SL, TP prices; SL=-1R; ambiguous/open-at-end=0R for raw net; conservative net subtracts 1R per ambiguous trade.",
        "source": os.getenv("COMBINED_SOURCE_URL"),
        "validation_start_utc": os.getenv("FVG_ONLY_START_UTC"),
        "validation_end_utc": os.getenv("FVG_ONLY_END_UTC"),
        "summary": summaries, "deltas_vs_midpoint": deltas, "variants": audits,
    }
    AUDIT_OUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUT.write_text(json.dumps(audit_payload, indent=2), encoding="utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(audit_payload, indent=2), encoding="utf-8")
    print(json.dumps(audit_payload, indent=2))


if __name__ == "__main__":
    if not os.getenv("COMBINED_SOURCE_URL"):
        raise RuntimeError("COMBINED_SOURCE_URL is required")
    main()
