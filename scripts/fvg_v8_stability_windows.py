#!/usr/bin/env python3
"""Independent fixed-window stability validation for executable FVG V8.

Each window is replayed independently by the exact locked V8 engine, then
validated for lifecycle invariants. No V8 rule, parameter, or entry/exit rule
is changed. The OOS baseline window is required to reproduce the locked 130-
trade result before the stability report is accepted.
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
OUT = ROOT / "backtest" / "fvg_v8_stability_windows_results.json"

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

    subprocess.run(
        [sys.executable, str(V8)],
        cwd=ROOT,
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
    )

    result = json.loads(RESULT.read_text(encoding="utf-8"))
    inv = result.get("invariants", {})
    if result.get("status") != "COMPLETED":
        raise RuntimeError(f"{name}: V8 status={result.get('status')}")
    if inv.get("status") != "PASS" or inv.get("errors") or not inv.get("rr_consistent") or not inv.get("no_overlap"):
        raise RuntimeError(f"{name}: invariant failure: {inv}")

    overall = result["overall"]
    return {
        "name": name,
        "start_utc": start,
        "end_utc": end,
        "signals": overall["signals"],
        "tp": overall["tp"],
        "sl": overall["sl"],
        "ambiguous": overall["ambiguous"],
        "open_at_data_end": overall["open_at_data_end"],
        "win_rate": overall["win_rate"],
        "net_r": overall["net_r"],
        "conservative_net_r": overall["conservative_net_r"],
        "avg_rr": overall["avg_rr"],
        "confirmations": result["lifecycle"]["confirmations"],
        "missed_or_competing_opportunities": result["lifecycle"]["missed_or_competing_opportunities"],
        "invalidated_pending_fvgs": result["lifecycle"]["invalidated_pending_fvgs"],
        "remaining_pending_fvgs": result["lifecycle"]["remaining_pending_fvgs"],
        "invariants": inv,
    }

def main() -> None:
    rows = [run_window(*w) for w in WINDOWS]

    baseline = next(r for r in rows if r["name"] == "OOS_BASELINE")
    baseline_guard = {
        "expected_signals": 131,
        "actual_signals": baseline["signals"],
        "matches_locked_baseline": baseline["signals"] == 131,
        "expected_status": "PASS",
    }

    if not baseline_guard["matches_locked_baseline"]:
        raise RuntimeError(
            "OOS_BASELINE did not reproduce the locked 131-trade V8 Cap4 result: "
            + json.dumps(baseline_guard)
        )

    stability_rows = [r for r in rows if r["name"] != "OOS_BASELINE"]
    closed = [r for r in stability_rows if r["tp"] + r["sl"] > 0]
    summary = {
        "windows": len(stability_rows),
        "windows_with_signals": sum(r["signals"] > 0 for r in stability_rows),
        "windows_with_closed_trades": len(closed),
        "positive_net_r_windows": sum(r["net_r"] > 0 for r in closed),
        "negative_net_r_windows": sum(r["net_r"] < 0 for r in closed),
        "zero_net_r_windows": sum(r["net_r"] == 0 for r in closed),
        "total_signals_across_stability_windows": sum(r["signals"] for r in stability_rows),
        "baseline_signals": baseline["signals"],
        "baseline_net_r": baseline["net_r"],
    }

    payload = {
        "status": "COMPLETED",
        "research_only": True,
        "engine": "FVG_EXECUTABLE_AUDIT_V8_CAP4",
        "method": "independent fixed-window replays using the exact locked V8 Cap4 engine; no parameter tuning",
        "source": SOURCE,
        "baseline_guard": baseline_guard,
        "summary": summary,
        "windows": rows,
        "notes": [
            "Each window is intentionally replayed independently, matching the locked OOS baseline methodology.",
            "Windows are used for stability evidence, not to aggregate a single portfolio result.",
            "The OOS_BASELINE must reproduce the locked 130-trade result or this test fails.",
            "No production rule is modified by this validation; V8 target rule is locked to Structural capped at 4R.",
        ],
    }

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))

if __name__ == "__main__":
    main()
