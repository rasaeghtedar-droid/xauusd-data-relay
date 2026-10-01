#!/usr/bin/env python3
"""Run the locked executable FVG V8 audit across fixed stability windows.

This is a validation wrapper only. It invokes the already-audited V8 engine
without changing any production/research rules and stores each window result.
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
    env["COMBINED_SOURCE_URL"] = os.getenv(
        "COMBINED_SOURCE_URL",
        "https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv",
    )
    subprocess.run([sys.executable, str(V8)], cwd=ROOT, env=env, check=True,
                   stdout=subprocess.DEVNULL)
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    if result.get("status") != "COMPLETED":
        raise RuntimeError(f"{name}: V8 status={result.get('status')}")
    inv = result.get("invariants", {})
    if inv.get("status") != "PASS" or inv.get("errors") or not inv.get("rr_consistent") or not inv.get("no_overlap"):
        raise RuntimeError(f"{name}: invariant failure: {inv}")
    return {
        "name": name,
        "start_utc": start,
        "end_utc": end,
        "signals": result["overall"]["signals"],
        "tp": result["overall"]["tp"],
        "sl": result["overall"]["sl"],
        "ambiguous": result["overall"]["ambiguous"],
        "open_at_data_end": result["overall"]["open_at_data_end"],
        "win_rate": result["overall"]["win_rate"],
        "net_r": result["overall"]["net_r"],
        "conservative_net_r": result["overall"]["conservative_net_r"],
        "avg_rr": result["overall"]["avg_rr"],
        "confirmations": result["lifecycle"]["confirmations"],
        "missed_or_competing_opportunities": result["lifecycle"]["missed_or_competing_opportunities"],
        "invalidated_pending_fvgs": result["lifecycle"]["invalidated_pending_fvgs"],
        "remaining_pending_fvgs": result["lifecycle"]["remaining_pending_fvgs"],
        "invariants": result["invariants"],
    }

def main() -> None:
    rows = [run_window(*w) for w in WINDOWS]
    closed = [r for r in rows if r["tp"] + r["sl"] > 0]
    summary = {
        "windows": len(rows),
        "windows_with_signals": sum(r["signals"] > 0 for r in rows),
        "windows_with_closed_trades": len(closed),
        "positive_net_r_windows": sum(r["net_r"] > 0 for r in closed),
        "negative_net_r_windows": sum(r["net_r"] < 0 for r in closed),
        "zero_net_r_windows": sum(r["net_r"] == 0 for r in closed),
        "total_signals": sum(r["signals"] for r in rows),
        "total_tp": sum(r["tp"] for r in rows),
        "total_sl": sum(r["sl"] for r in rows),
        "total_ambiguous": sum(r["ambiguous"] for r in rows),
        "total_net_r": round(sum(r["net_r"] for r in rows), 2),
        "total_conservative_net_r": round(sum(r["conservative_net_r"] for r in rows), 2),
    }
    payload = {
        "status": "COMPLETED",
        "research_only": True,
        "engine": "FVG_EXECUTABLE_AUDIT_V8",
        "method": "fixed-window replay of the locked V8 engine; no parameter tuning",
        "source": os.getenv(
            "COMBINED_SOURCE_URL",
            "https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv",
        ),
        "summary": summary,
        "windows": rows,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))

if __name__ == "__main__":
    main()
