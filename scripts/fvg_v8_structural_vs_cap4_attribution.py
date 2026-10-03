#!/usr/bin/env python3
"""Structural-vs-Cap4 lifecycle attribution diagnostic.

Runs the exact V8 replay twice per fixed window:
1) original structural target
2) min(structural target, 4R)

No entry, SL, FVG, lifecycle, queue, or lookahead rule is changed.
The diagnostic attributes the difference to:
- target-only changes on common trades
- earlier exits / released trade slots
- Cap4-only trades
- Structural-only trades

Research-only.
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fvg_executable_audit_v8 as v8
import fvg_only_gold_hunter_backtest as base

SOURCE = os.getenv(
    "COMBINED_SOURCE_URL",
    "https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv",
)
OUT = ROOT / "backtest" / "fvg_v8_structural_vs_cap4_attribution.json"

WINDOWS = [
    ("PRE_OOS_MAY_JUL", "2026-05-01T00:00:00+00:00", "2026-07-31T23:59:59+00:00"),
    ("JUN_2026", "2026-06-01T00:00:00+00:00", "2026-06-30T23:59:59+00:00"),
    ("JUL_2026", "2026-07-01T00:00:00+00:00", "2026-07-31T23:59:59+00:00"),
    ("AUG_2026", "2026-08-01T00:00:00+00:00", "2026-08-31T23:59:59+00:00"),
    ("SEP_2026_PARTIAL", "2026-09-01T00:00:00+00:00", "2026-09-09T23:59:59+00:00"),
    ("OOS_BASELINE", "2026-08-01T00:00:00+00:00", "2026-09-09T23:59:59+00:00"),
]

def run_model(start, end, cap4):
    old_target = v8.target
    def capped(direction, entry, sl, ctx):
        structural = old_target(direction, entry, sl, ctx)
        if structural is None or not cap4:
            return structural
        risk = entry - sl if direction == "BUY" else sl - entry
        cap_tp = entry + 4.0 * risk if direction == "BUY" else entry - 4.0 * risk
        return min(structural, cap_tp) if direction == "BUY" else max(structural, cap_tp)
    v8.target = capped
    old_start, old_end, old_source = v8.START, v8.END, v8.SOURCE
    old_url = base.URL
    v8.START, v8.END, v8.SOURCE = start, end, SOURCE
    base.URL = SOURCE
    try:
        v8.main()
        result = json.loads((ROOT / "backtest/fvg_executable_audit_v8_results.json").read_text())
        return result
    finally:
        v8.target = old_target
        v8.START, v8.END, v8.SOURCE = old_start, old_end, old_source
        base.URL = old_url

def identity(t):
    return (t["direction"], t["formation_time"], t["confirmation_time"])

def realized(t):
    if t["outcome"] == "TP":
        return t["rr"]
    if t["outcome"] == "SL":
        return -1.0
    return 0.0

def analyze(structural, cap4):
    a = {identity(t): t for t in structural["trades"]}
    b = {identity(t): t for t in cap4["trades"]}
    common = sorted(set(a) & set(b))
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))

    common_rows = []
    for k in common:
        x, y = a[k], b[k]
        common_rows.append({
            "identity": k,
            "direction": x["direction"],
            "structural_target_rr": round(x["rr"], 2),
            "cap4_target_rr": round(y["rr"], 2),
            "target_was_capped": y["rr"] < x["rr"] - 1e-9,
            "structural_outcome": x["outcome"],
            "cap4_outcome": y["outcome"],
            "structural_exit_time": x["exit_time"],
            "cap4_exit_time": y["exit_time"],
            "structural_realized_r": round(realized(x), 2),
            "cap4_realized_r": round(realized(y), 2),
            "delta_r": round(realized(y) - realized(x), 2),
        })

    capped_common = [r for r in common_rows if r["target_was_capped"]]
    early_release = [
        r for r in capped_common
        if r["structural_exit_time"] and r["cap4_exit_time"]
        and r["cap4_exit_time"] < r["structural_exit_time"]
    ]
    common_delta = round(sum(r["delta_r"] for r in common_rows), 2)
    target_effect_r = common_delta
    target_positive_r = round(sum(r["delta_r"] for r in capped_common if r["delta_r"] > 0), 2)
    target_negative_r = round(sum(r["delta_r"] for r in capped_common if r["delta_r"] < 0), 2)
    target_zero_count = sum(r["delta_r"] == 0 for r in capped_common)
    early_release_count = len(early_release)
    only_a_r = round(sum(realized(a[k]) for k in only_a), 2)
    only_b_r = round(sum(realized(b[k]) for k in only_b), 2)
    gross_delta_r = round(cap4["overall"]["net_r"] - structural["overall"]["net_r"], 2)
    decomposition_r = round(target_effect_r + only_b_r - only_a_r, 2)

    return {
        "structural_signals": len(a),
        "cap4_signals": len(b),
        "common_signals": len(common),
        "structural_only_signals": len(only_a),
        "cap4_only_signals": len(only_b),
        "common_target_capped": len(capped_common),
        "common_early_exit_count": len(early_release),
        "common_delta_r": common_delta,
        "target_effect_r": target_effect_r,
        "target_effect_positive_r": target_positive_r,
        "target_effect_negative_r": target_negative_r,
        "target_effect_zero_count": target_zero_count,
        "early_release_count": early_release_count,
        "structural_only_realized_r": only_a_r,
        "cap4_only_realized_r": only_b_r,
        "gross_net_delta_r": gross_delta_r,
        "decomposition_r": decomposition_r,
        "decomposition_check": decomposition_r == gross_delta_r,
        "cap4_net_r": cap4["overall"]["net_r"],
        "structural_net_r": structural["overall"]["net_r"],
        "common_trade_details": common_rows,
    }

def main():
    rows = []
    for name, start, end in WINDOWS:
        structural = run_model(start, end, False)
        cap4 = run_model(start, end, True)
        for r in (structural, cap4):
            inv = r["invariants"]
            if r["status"] != "COMPLETED" or inv["status"] != "PASS" or inv["errors"] or not inv["rr_consistent"] or not inv["no_overlap"]:
                raise RuntimeError(f"{name}: V8 invariant failure")
        rows.append({
            "name": name,
            "start_utc": start,
            "end_utc": end,
            "structural": {
                "signals": structural["overall"]["signals"],
                "tp": structural["overall"]["tp"],
                "sl": structural["overall"]["sl"],
                "ambiguous": structural["overall"]["ambiguous"],
                "net_r": structural["overall"]["net_r"],
                "win_rate": structural["overall"]["win_rate"],
            },
            "cap4": {
                "signals": cap4["overall"]["signals"],
                "tp": cap4["overall"]["tp"],
                "sl": cap4["overall"]["sl"],
                "ambiguous": cap4["overall"]["ambiguous"],
                "net_r": cap4["overall"]["net_r"],
                "win_rate": cap4["overall"]["win_rate"],
            },
            "attribution": analyze(structural, cap4),
        })

    oos = next(x for x in rows if x["name"] == "OOS_BASELINE")
    payload = {
        "status": "COMPLETED",
        "research_only": True,
        "purpose": "Attribute Structural-vs-Cap4 performance difference to target-only effects versus earlier trade release and cohort changes.",
        "target_rule_cap4": "min(structural target, 4R)",
        "source": SOURCE,
        "windows": rows,
        "invariants": {
            "all_windows_completed": True,
            "all_v8_invariants_pass": True,
            "no_rule_changes_except_target_cap": True,
            "oos_structural_signals": oos["structural"]["signals"],
            "oos_cap4_signals": oos["cap4"]["signals"],
        },
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))

if __name__ == "__main__":
    main()
