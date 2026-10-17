#!/usr/bin/env python3
"""Research-only V8 Cap4 FVG entry-depth comparison with fine execution-cost sensitivity."""
from __future__ import annotations

import json
import os
from pathlib import Path

import scripts.fvg_executable_audit_v8 as v8

RESULT = Path("backtest/fvg_executable_audit_v8_results.json")
OUT = Path("backtest/fvg_90_depth_comparison.json")
ORIGINAL_FVG_AT = v8.fvg_at
COST_R_SCENARIOS = tuple(round(i / 100, 2) for i in range(0, 31))


def run_model(label: str, depth: float | None) -> dict:
    """Run one entry variant and validate persisted entries against their zones."""
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
            if adjusted["direction"] == "BUY":
                adjusted["mid"] = adjusted["hi"] - depth * width
            else:
                adjusted["mid"] = adjusted["lo"] + depth * width
            return adjusted

        v8.fvg_at = fvg_at_depth

    v8.main()
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
        raise RuntimeError(f"{label}: entry-level validation failed: {mismatches[:10]}")

    result["_entry_validation"] = {
        "status": "PASS",
        "trades_checked": len(trades),
        "entry_definition": "midpoint" if depth is None else f"{int(depth * 100)}%-depth",
    }
    return result


def raw_net_and_ambiguous(result: dict) -> tuple[float, int, int]:
    trades = result["trades"]
    raw_net = sum(
        float(t.get("rr", 0)) if t.get("outcome") == "TP"
        else -1.0 if t.get("outcome") == "SL"
        else 0.0
        for t in trades
    )
    ambiguous = sum(t.get("outcome") == "AMBIGUOUS" for t in trades)
    return raw_net, ambiguous, len(trades)


def summarize(result: dict) -> dict:
    overall = result["overall"]
    raw_net, ambiguous, n = raw_net_and_ambiguous(result)
    sensitivity = {}
    for cost_r in COST_R_SCENARIOS:
        total_cost = n * cost_r
        sensitivity[f"{cost_r:.2f}R_per_trade"] = {
            "assumed_round_trip_cost_r_per_trade": cost_r,
            "total_cost_r": round(total_cost, 2),
            "net_r_after_cost": round(raw_net - total_cost, 2),
            "conservative_net_r_after_cost": round(raw_net - ambiguous - total_cost, 2),
        }
    return {
        "signals": overall["signals"],
        "tp": overall["tp"],
        "sl": overall["sl"],
        "ambiguous": overall["ambiguous"],
        "open_at_data_end": overall.get("open_at_data_end", 0),
        "win_rate": overall["win_rate"],
        "net_r_before_cost": overall["net_r"],
        "conservative_net_r_before_cost": overall["conservative_net_r"],
        "avg_rr": overall["avg_rr"],
        "break_even_round_trip_cost_r_per_trade": round(raw_net / n, 4) if n else None,
        "cost_sensitivity": sensitivity,
        "invariants": result["invariants"],
        "entry_validation": result["_entry_validation"],
        "lifecycle": result["lifecycle"],
    }


def main() -> None:
    variants = [("midpoint", None), ("depth_70", 0.70), ("depth_80", 0.80), ("depth_90", 0.90)]
    results = {label: run_model(label.upper(), depth) for label, depth in variants}
    baseline_raw, baseline_amb, baseline_n = raw_net_and_ambiguous(results["midpoint"])
    depth70_raw, depth70_amb, depth70_n = raw_net_and_ambiguous(results["depth_70"])

    crossover = None
    if depth70_n != baseline_n:
        # Equal cost is charged per signal; solve N70*(net70/N70 - c) = Nbase*(netbase/Nbase - c)
        # Equivalent: raw_net70 - c*N70 = raw_netbase - c*Nbase.
        crossover = round((depth70_raw - baseline_raw) / (depth70_n - baseline_n), 4)

    comparison = {}
    for label, _ in variants[1:]:
        current = results[label]["overall"]
        baseline = results["midpoint"]["overall"]
        comparison[label] = {
            "signals_delta": current["signals"] - baseline["signals"],
            "win_rate_percentage_points_delta": round((current["win_rate"] or 0) - (baseline["win_rate"] or 0), 2),
            "net_r_delta": round(current["net_r"] - baseline["net_r"], 2),
            "conservative_net_r_delta": round(current["conservative_net_r"] - baseline["conservative_net_r"], 2),
            "avg_rr_delta": round((current["avg_rr"] or 0) - (baseline["avg_rr"] or 0), 2),
        }

    payload = {
        "status": "COMPLETED",
        "research_only": True,
        "execution_cost_method": "Sensitivity analysis deducting a fixed round-trip cost in R per signal. This is not a broker-specific spread/slippage estimate.",
        "cost_r_scenarios_per_trade": list(COST_R_SCENARIOS),
        "depth70_vs_midpoint_equal_cost_crossover_r_per_trade": crossover,
        "definition": {
            "BUY": "entry = FVG high - depth * width",
            "SELL": "entry = FVG low + depth * width",
            "tested_depths": [70, 80, 90],
            "unchanged": [
                "SL boundary plus/minus PAD", "structural target capped at 4R",
                "minimum RR 2.0", "one active trade", "no queue", "no lookahead",
                "exit after entry candle", "same M5 data and validation window",
            ],
        },
        "source": os.getenv("COMBINED_SOURCE_URL"),
        "validation_start_utc": os.getenv("FVG_ONLY_START_UTC"),
        "validation_end_utc": os.getenv("FVG_ONLY_END_UTC"),
        "results": {label: summarize(result) for label, result in results.items()},
        "delta_vs_midpoint": comparison,
        "trades": {label: result["trades"] for label, result in results.items()},
        "decision_note": "Research only; do not merge any entry change into main without separate review.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    if not os.getenv("COMBINED_SOURCE_URL"):
        raise RuntimeError("COMBINED_SOURCE_URL is required")
    main()
