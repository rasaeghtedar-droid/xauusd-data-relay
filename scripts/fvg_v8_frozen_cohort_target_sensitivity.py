import csv
import io
import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path

SOURCE = os.getenv("COMBINED_SOURCE_URL")
START = os.getenv("FVG_ONLY_START_UTC")
END = os.getenv("FVG_ONLY_END_UTC")
AUDIT_PATH = Path("backtest/fvg_executable_audit_v8_results.json")


def pt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_m5():
    raw = urllib.request.urlopen(SOURCE, timeout=60).read().decode()
    rows = []
    for z in csv.DictReader(io.StringIO(raw)):
        t = z["datetime"]
        t = t if t.endswith("+00:00") else t + "+00:00"
        rows.append({
            "openTime": t,
            "open": float(z["open"]),
            "high": float(z["high"]),
            "low": float(z["low"]),
            "close": float(z["close"]),
        })
    rows.sort(key=lambda x: x["openTime"])
    if START:
        rows = [x for x in rows if pt(x["openTime"]) >= pt(START)]
    if END:
        rows = [x for x in rows if pt(x["openTime"]) <= pt(END)]
    return rows


def outcome(direction, entry, sl, tp, m5, entry_index):
    for k in range(entry_index + 1, len(m5)):
        b = m5[k]
        sl_hit = b["low"] <= sl if direction == "BUY" else b["high"] >= sl
        tp_hit = b["high"] >= tp if direction == "BUY" else b["low"] <= tp
        if sl_hit and tp_hit:
            return "AMBIGUOUS", b["openTime"]
        if tp_hit:
            return "TP", b["openTime"]
        if sl_hit:
            return "SL", b["openTime"]
    return "OPEN_AT_DATA_END", None


def capped_target(direction, entry, sl, structural_tp, cap):
    if cap is None:
        return structural_tp
    risk = entry - sl if direction == "BUY" else sl - entry
    cap_tp = entry + cap * risk if direction == "BUY" else entry - cap * risk
    return min(structural_tp, cap_tp) if direction == "BUY" else max(structural_tp, cap_tp)


def evaluate(name, trades, m5, cap):
    results = []
    for t in trades:
        direction = t["direction"]
        entry = t.get("_raw_entry", t["entry"])
        sl = t.get("_raw_sl", t["sl"])
        structural_tp = t.get("_raw_tp", t["tp"])
        tp = capped_target(direction, entry, sl, structural_tp, cap)
        rr = ((tp - entry) / (entry - sl)
              if direction == "BUY"
              else (entry - tp) / (sl - entry))
        out, exit_time = outcome(direction, entry, sl, tp, m5, t["entry_index"])
        results.append({
            "direction": direction,
            "formation_time": t["formation_time"],
            "confirmation_time": t["confirmation_time"],
            "entry_time": t["entry_time"],
            "entry_index": t["entry_index"],
            "entry": round(entry, 3),
            "sl": round(sl, 3),
            "structural_tp": round(structural_tp, 3),
            "tp": round(tp, 3),
            "rr": round(rr, 2),
            "outcome": out,
            "exit_time": exit_time,
        })

    tp_n = sum(x["outcome"] == "TP" for x in results)
    sl_n = sum(x["outcome"] == "SL" for x in results)
    amb_n = sum(x["outcome"] == "AMBIGUOUS" for x in results)
    open_n = sum(x["outcome"] == "OPEN_AT_DATA_END" for x in results)
    net = sum(x["rr"] if x["outcome"] == "TP" else -1 if x["outcome"] == "SL" else 0 for x in results)
    closed = tp_n + sl_n
    return {
        "model": name,
        "target_rule": "original structural target" if cap is None else f"min(structural target, {cap}R cap)",
        "signals": len(results),
        "tp": tp_n,
        "sl": sl_n,
        "ambiguous": amb_n,
        "open_at_data_end": open_n,
        "win_rate": round(100 * tp_n / closed, 2) if closed else None,
        "net_r": round(net, 2),
        "conservative_net_r": round(net - amb_n, 2),
        "avg_realized_rr": round(sum(x["rr"] for x in results) / len(results), 2) if results else None,
        "trades": results,
    }


def main():
    if not SOURCE:
        raise RuntimeError("COMBINED_SOURCE_URL is required")

    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    locked = audit["trades"]
    m5 = load_m5()

    # Frozen cohort guard: every model MUST evaluate the exact V8 trade identities.
    identities = [(x["direction"], x["formation_time"], x["confirmation_time"]) for x in locked]
    models = [
        evaluate("STRUCTURAL", locked, m5, None),
        evaluate("RR_CAP_2", locked, m5, 2.0),
        evaluate("RR_CAP_3", locked, m5, 3.0),
        evaluate("RR_CAP_4", locked, m5, 4.0),
        evaluate("RR_CAP_5", locked, m5, 5.0),
    ]

    errors = []
    for m in models:
        got = [(x["direction"], x["formation_time"], x["confirmation_time"]) for x in m["trades"]]
        if got != identities:
            errors.append(f"COHORT_MISMATCH:{m['model']}")
        if len(m["trades"]) != len(locked):
            errors.append(f"SIGNAL_COUNT_MISMATCH:{m['model']}")
        if any(x["rr"] < 1.99 for x in m["trades"]):
            errors.append(f"RR_BELOW_2:{m['model']}")

    result = {
        "status": "COMPLETED" if not errors else "FAILED_INVARIANTS",
        "research_only": True,
        "purpose": "Measure target-only performance sensitivity on the exact locked V8 cohort without allowing target changes to create or remove trades.",
        "source": SOURCE,
        "validation_start_utc": START,
        "validation_end_utc": END,
        "locked_cohort": {
            "source": "backtest/fvg_executable_audit_v8_results.json",
            "signals": len(locked),
            "identity_fields": ["direction", "formation_time", "confirmation_time"],
            "frozen": True,
        },
        "models": models,
        "invariants": {
            "status": "PASS" if not errors else "FAIL",
            "errors": errors,
            "all_models_same_cohort": not any(x.startswith("COHORT_") for x in errors),
            "all_models_same_signal_count": not any(x.startswith("SIGNAL_COUNT_") for x in errors),
        },
    }
    out = Path("backtest/fvg_v8_frozen_cohort_target_sensitivity_results.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
