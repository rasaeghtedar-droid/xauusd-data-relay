#!/usr/bin/env python3
"""Locked invariant verifier for executable FVG audit v4.

No strategy changes. This only validates that the already-produced v4 result
is internally consistent and temporally ordered.
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime

RESULT = Path("backtest/fvg_temporal_audit_v4_results.json")
TOL = 0.01

def pt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z","+00:00"))

def main():
    if not RESULT.exists():
        raise FileNotFoundError(str(RESULT))
    r = json.loads(RESULT.read_text(encoding="utf-8"))
    trades = r.get("trades", [])
    errors = []
    checks = {
        "entry_is_confirmation_close": r.get("rules", {}).get("entry") == "confirmation candle close after candle fully closes",
        "confirmation_candle_cannot_exit": r.get("rules", {}).get("exit_starts") == "first closed candle strictly after entry",
        "one_active_trade": r.get("rules", {}).get("one_active_trade") is True,
        "queue_false": r.get("rules", {}).get("queue") is False,
        "lookahead_false": r.get("rules", {}).get("lookahead") is False,
    }
    for name, ok in checks.items():
        if not ok:
            errors.append(f"rule check failed: {name}")

    prev_exit = None
    seen = set()
    for i, t in enumerate(trades):
        key=(t.get("direction"),t.get("formation_time"),t.get("confirmation_time"))
        if key in seen:
            errors.append(f"duplicate trade identity at index {i}: {key}")
        seen.add(key)

        if t.get("confirmation_time") != t.get("entry_time"):
            errors.append(f"entry/confirmation mismatch at {i}")

        if t.get("outcome") in {"TP","SL","AMBIGUOUS"}:
            if not t.get("exit_time"):
                errors.append(f"closed trade missing exit_time at {i}")
            elif pt(t["exit_time"]) <= pt(t["entry_time"]):
                errors.append(f"exit not strictly after entry at {i}")

        if prev_exit is not None and pt(t["entry_time"]) <= prev_exit:
            errors.append(f"trade overlap at {i}")

        d=t["direction"]; entry=float(t["entry"]); sl=float(t["sl"]); tp=float(t["tp"])
        rr=(tp-entry)/(entry-sl) if d=="BUY" else (entry-tp)/(sl-entry)
        if abs(round(rr,2)-float(t["rr"])) > TOL:
            errors.append(f"RR mismatch at {i}: recomputed={rr:.6f}, stored={t['rr']}")

        if d=="BUY" and not (sl < entry < tp):
            errors.append(f"BUY price ordering invalid at {i}")
        if d=="SELL" and not (tp < entry < sl):
            errors.append(f"SELL price ordering invalid at {i}")
        if rr < 2.0 - 1e-9:
            errors.append(f"RR<2 at {i}: {rr}")

        if t.get("outcome") in {"TP","SL","AMBIGUOUS"}:
            prev_exit = pt(t["exit_time"])

    out={
        "status":"PASS" if not errors else "FAIL",
        "result_file":str(RESULT),
        "trade_count":len(trades),
        "errors":errors,
        "checks":checks,
        "summary":{
            "all_rr_consistent":not any("RR mismatch" in e for e in errors),
            "all_exits_strictly_after_entry":not any("exit not strictly after entry" in e for e in errors),
            "no_overlap":not any("trade overlap" in e for e in errors),
            "all_rr_ge_2":not any("RR<2" in e for e in errors),
        }
    }
    p=Path("backtest/fvg_executable_v4_invariant_verification.json")
    p.write_text(json.dumps(out,indent=2),encoding="utf-8")
    print(json.dumps(out,indent=2))
    if errors:
        raise SystemExit(1)

if __name__=="__main__":
    main()
