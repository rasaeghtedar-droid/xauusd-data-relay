#!/usr/bin/env python3
"""Research-only V8 Cap4 midpoint vs 90%-depth FVG entry comparison."""
from __future__ import annotations

import json
import os
from pathlib import Path

import scripts.fvg_executable_audit_v8 as v8

RESULT = Path("backtest/fvg_executable_audit_v8_results.json")
OUT = Path("backtest/fvg_90_depth_comparison.json")
ORIGINAL_FVG_AT = v8.fvg_at


def run_model(label: str, depth90: bool) -> dict:
    """Run one variant and validate every persisted entry against its zone."""
    v8.fvg_at = ORIGINAL_FVG_AT

    if depth90:
        def fvg_at_depth(candles, i):
            fvg = ORIGINAL_FVG_AT(candles, i)
            if not fvg:
                return fvg
            adjusted = dict(fvg)
            width = adjusted["hi"] - adjusted["lo"]
            if width <= 0:
                return None
            # A 90% retracement from the near edge: BUY descends from zone high;
            # SELL ascends from zone low. The engine consumes this value as mid.
            if adjusted["direction"] == "BUY":
                adjusted["mid"] = adjusted["hi"] - 0.90 * width
            else:
                adjusted["mid"] = adjusted["lo"] + 0.90 * width
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
        lo, hi = trade.get("zone_lo"), trade.get("zone_hi")
        entry = trade.get("entry")
        direction = trade.get("direction")
        if None in (lo, hi, entry) or hi <= lo:
            mismatches.append(f"TRADE_ZONE_MISSING:{index}")
            continue
        width = hi - lo
        if depth90:
            expected = hi - 0.90 * width if direction == "BUY" else lo + 0.90 * width
        else:
            expected = (lo + hi) / 2
        if abs(float(entry) - round(expected, 3)) > 0.001:
            mismatches.append(f"ENTRY_LEVEL_MISMATCH:{index}")
    if mismatches:
        raise RuntimeError(f"{label}: entry-level validation failed: {mismatches[:10]}")

    result["_entry_validation"] = {
        "status": "PASS",
        "trades_checked": len(trades),
        "entry_definition": "90%-depth" if depth90 else "midpoint",
    }
    return result


def main() -> None:
    baseline = run_model("BASELINE_MIDPOINT", False)
    depth = run_model("FVG_90_PERCENT_DEPTH", True)
    b, d = baseline["overall"], depth["overall"]

    payload = {
        "status": "COMPLETED",
        "research_only": True,
        "definition": {
            "BUY": "entry = FVG high - 0.90 * width",
            "SELL": "entry = FVG low + 0.90 * width",
            "unchanged": [
                "SL boundary plus/minus PAD",
                "structural target capped at 4R",
                "minimum RR 2.0",
                "one active trade",
                "no queue",
                "no lookahead",
                "exit after entry candle",
                "same M5 data and validation window",
            ],
        },
        "source": os.getenv("COMBINED_SOURCE_URL"),
        "validation_start_utc": os.getenv("FVG_ONLY_START_UTC"),
        "validation_end_utc": os.getenv("FVG_ONLY_END_UTC"),
        "baseline": {
            "overall": b,
            "lifecycle": baseline["lifecycle"],
            "invariants": baseline["invariants"],
            "entry_validation": baseline["_entry_validation"],
        },
        "fvg_90_depth": {
            "overall": d,
            "lifecycle": depth["lifecycle"],
            "invariants": depth["invariants"],
            "entry_validation": depth["_entry_validation"],
        },
        "delta_90_minus_baseline": {
            "signals": d["signals"] - b["signals"],
            "win_rate_percentage_points": round((d["win_rate"] or 0) - (b["win_rate"] or 0), 2),
            "net_r": round(d["net_r"] - b["net_r"], 2),
            "conservative_net_r": round(d["conservative_net_r"] - b["conservative_net_r"], 2),
            "avg_rr": round((d["avg_rr"] or 0) - (b["avg_rr"] or 0), 2),
        },
        "trades": {"baseline": baseline["trades"], "fvg_90_depth": depth["trades"]},
        "decision_note": "Research only; do not merge into main without review.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    if not os.getenv("COMBINED_SOURCE_URL"):
        raise RuntimeError("COMBINED_SOURCE_URL is required")
    main()
