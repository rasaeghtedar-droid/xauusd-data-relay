#!/usr/bin/env python3
"""Fixed-window stability reporting for the locked executable FVG V8 engine.

Each replay starts at the first available source candle and ends at the end of
the available dataset. Reporting windows filter trades by confirmation time.
This preserves the full prior-state/history of the V8 state machine and avoids
warmup or boundary-state artifacts. No V8 rules are changed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V8 = ROOT / "scripts" / "fvg_executable_audit_v8.py"
RESULT = ROOT / "backtest" / "fvg_executable_audit_v8_results.json"
OUT = ROOT / "backtest" / "fvg_v8_stability_windows_results.json"

FULL_START = "2026-02-02T03:20:00+00:00"
FULL_END = "2026-09-09T23:59:59+00:00"

WINDOWS = [
    ("PRE_OOS_MAY_JUL", "2026-05-01T00:00:00+00:00", "2026-07-31T23:59:59+00:00"),
    ("JUN_2026", "2026-06-01T00:00:00+00:00", "2026-06-30T23:59:59+00:00"),
    ("JUL_2026", "2026-07-01T00:00:00+00:00", "2026-07-31T23:59:59+00:00"),
    ("AUG_2026", "2026-08-01T00:00:00+00:00", "2026-08-31T23:59:59+00:00"),
    ("SEP_2026_PARTIAL", "2026-09-01T00:00:00+00:00", "2026-09-09T23:59:59+00:00"),
    ("OOS_BASELINE", "2026-08-01T00:00:00+00:00", "2026-09-09T23:59:59+00:00"),
]

def pt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def run_full_replay() -> dict:
    env = os.environ.copy()
    env["FVG_ONLY_START_UTC"] = FULL_START
    env["FVG_ONLY_END_UTC"] = FULL_END
    env["COMBINED_SOURCE_URL"] = os.getenv(
        "COMBINED_SOURCE_URL",
        "https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv",
    )
    subprocess.run(
        [sys.executable, str(V8)],
        cwd=ROOT,
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    if result.get("status") != "COMPLETED":
        raise RuntimeError(f"V8 status={result.get('status')}")
    inv = result.get("invariants", {})
    if inv.get("status") != "PASS" or inv.get("errors") or not inv.get("rr_consistent") or not inv.get("no_overlap"):
        raise RuntimeError(f"V8 invariant failure: {inv}")
    return result

def summarize(window, trades):
    start = pt(window[1]); end = pt(window[2])
    ss = [t for t in trades if start <= pt(t["confirmation_time"]) <= end]
    tp = sum(t["outcome"] == "TP" for t in ss)
    sl = sum(t["outcome"] == "SL" for t in ss)
    amb = sum(t["outcome"] == "AMBIGUOUS" for t in ss)
    op = sum(t["outcome"] == "OPEN_AT_DATA_END" for t in ss)
    net = sum(t["rr"] if t["outcome"] == "TP" else -1 if t["outcome"] == "SL" else 0 for t in ss)
    return {
        "name": window[0],
        "start_utc": window[1],
        "end_utc": window[2],
        "signals": len(ss),
        "tp": tp,
        "sl": sl,
        "ambiguous": amb,
        "open_at_data_end": op,
        "win_rate": round(100 * tp / (tp + sl), 2) if tp + sl else None,
        "net_r": round(net, 2),
        "conservative_net_r": round(net - amb, 2),
        "avg_rr": round(sum(t["rr"] for t in ss) / len(ss), 2) if ss else None,
        "invariants": {
            "status": "PASS",
            "errors": [],
            "rr_consistent": True,
            "no_overlap": True,
        },
    }

def main() -> None:
    result = run_full_replay()
    rows = [summarize(w, result["trades"]) for w in WINDOWS]
    payload = {
        "status": "COMPLETED",
        "research_only": True,
        "engine": "FVG_EXECUTABLE_AUDIT_V8",
        "method": "one full-history V8 replay; report fixed confirmation-time windows; no parameter tuning",
        "source": os.getenv(
            "COMBINED_SOURCE_URL",
            "https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv",
        ),
        "full_replay": {
            "start_utc": FULL_START,
            "end_utc": FULL_END,
            "signals": result["overall"]["signals"],
            "tp": result["overall"]["tp"],
            "sl": result["overall"]["sl"],
            "ambiguous": result["overall"]["ambiguous"],
            "net_r": result["overall"]["net_r"],
            "invariants": result["invariants"],
        },
        "windows": rows,
        "notes": [
            "All windows are slices of the same full-history state-machine replay.",
            "Confirmation time is the window membership key.",
            "This avoids restarting V8 at each window and avoids boundary-state/warmup artifacts.",
            "OOS_BASELINE should reconcile exactly to the locked 130-trade V8 result.",
        ],
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))

if __name__ == "__main__":
    main()
