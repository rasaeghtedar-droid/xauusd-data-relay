#!/usr/bin/env python3
"""Independent replay of FVG V8 with a 4R maximum target.

Only the target is capped. Entry, FVG lifecycle, confirmation, SL, one-active-trade,
no-queue, no-lookahead, and exit timing remain the exact V8 engine rules.
This is research-only and intentionally does not modify the frozen V8 engine.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V8 = ROOT / "scripts" / "fvg_executable_audit_v8.py"
RESULT = ROOT / "backtest" / "fvg_executable_audit_v8_results.json"
OUT = ROOT / "backtest" / "fvg_v8_cap4_stability_results.json"

SOURCE = os.getenv(
    "COMBINED_SOURCE_URL",
    "https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv",
)

WINDOWS = [
    ("PRE_OOS_MAY_JUL", "2026-05-01T00:00:00+00:00", "2026-07-31T23:59:59+00:00"),
    ("JUN_2026", "2026-06-01T00:00:00+00:00", "2026-06-30T23:59:59+00:00"),
    ("JUL_2026", "2026-07-01T00:00:00+00:00", "2026-07-31T23:59:59+00:00"),
    ("AUG_2026", "2026-08-01T00:00:00+00:00", "2026-08-31T23:59:59+00:00"),
    ("SEP_2026_PARTIAL", "2026-09-01T00:00:00+00:00", "2026-09-09T23:59:59+00:00"),
    ("OOS_BASELINE", "2026-08-01T00:00:00+00:00", "2026-09-09T23:59:59+00:00"),
]

def run_window(name: str, start: str, end: str) -> dict:
    env = os.environ.copy()
    env["FVG_ONLY_START_UTC"] = start
    env["FVG_ONLY_END_UTC"] = end
    env["COMBINED_SOURCE_URL"] = SOURCE
    code = (
        "import scripts.fvg_executable_audit_v8 as v8\n"
        "original=v8.target\n"
        "def capped(direction, entry, sl, ctx):\n"
        "    structural=original(direction, entry, sl, ctx)\n"
        "    if structural is None: return None\n"
        "    risk=entry-sl if direction=='BUY' else sl-entry\n"
        "    cap_tp=entry+4.0*risk if direction=='BUY' else entry-4.0*risk\n"
        "    return min(structural, cap_tp) if direction=='BUY' else max(structural, cap_tp)\n"
        "v8.target=capped\n"
        "v8.main()\n"
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    inv = result.get("invariants", {})
    if result.get("status") != "COMPLETED":
        raise RuntimeError(f"{name}: status={result.get('status')}")
    if inv.get("status") != "PASS" or inv.get("errors") or not inv.get("rr_consistent") or not inv.get("no_overlap"):
        raise RuntimeError(f"{name}: invariant failure: {inv}")
    o = result["overall"]
    return {
        "name": name, "start_utc": start, "end_utc": end,
        "signals": o["signals"], "tp": o["tp"], "sl": o["sl"],
        "ambiguous": o["ambiguous"], "open_at_data_end": o["open_at_data_end"],
        "win_rate": o["win_rate"], "net_r": o["net_r"],
        "conservative_net_r": o["conservative_net_r"], "avg_rr": o["avg_rr"],
        "confirmations": result["lifecycle"]["confirmations"],
        "missed_or_competing_opportunities": result["lifecycle"]["missed_or_competing_opportunities"],
        "invalidated_pending_fvgs": result["lifecycle"]["invalidated_pending_fvgs"],
        "remaining_pending_fvgs": result["lifecycle"]["remaining_pending_fvgs"],
        "invariants": inv,
    }

def main():
    rows = [run_window(*w) for w in WINDOWS]
    stability = [r for r in rows if r["name"] != "OOS_BASELINE"]
    baseline = next(r for r in rows if r["name"] == "OOS_BASELINE")
    closed = [r for r in stability if r["tp"] + r["sl"] > 0]
    summary = {
        "windows": len(stability),
        "windows_with_signals": sum(r["signals"] > 0 for r in stability),
        "windows_with_closed_trades": len(closed),
        "positive_net_r_windows": sum(r["net_r"] > 0 for r in closed),
        "negative_net_r_windows": sum(r["net_r"] < 0 for r in closed),
        "zero_net_r_windows": sum(r["net_r"] == 0 for r in closed),
        "total_signals_across_stability_windows": sum(r["signals"] for r in stability),
        "oos_baseline_signals_cap4": baseline["signals"],
        "oos_baseline_net_r_cap4": baseline["net_r"],
    }
    payload = {
        "status": "COMPLETED",
        "research_only": True,
        "engine": "FVG_EXECUTABLE_AUDIT_V8_WITH_TARGET_CAP_4R",
        "target_rule": "min(original_structural_target, 4R cap)",
        "method": "independent fixed-window replays using the exact V8 lifecycle engine; target cap only",
        "source": SOURCE,
        "summary": summary,
        "windows": rows,
        "invariants": {
            "all_windows_completed": True,
            "all_windows_v8_invariants_pass": all(r["invariants"]["status"] == "PASS" for r in rows),
            "no_rule_changes_except_target_cap": True,
        },
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))

if __name__ == "__main__":
    main()
