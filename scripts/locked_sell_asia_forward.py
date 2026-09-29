#!/usr/bin/env python3
import json, importlib.util
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "xauusd_analysis.json"
STATE = ROOT / "data" / "locked_sell_asia_forward_state.json"
OUT = ROOT / "data" / "locked_sell_asia_forward_signal.json"

def dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)

def load_engine():
    p = ROOT / "liquidity_hunter" / "liquidity_hunter.py"
    spec = importlib.util.spec_from_file_location("lh", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    latest = data["intervals"]["5m"]["latestClosed"]
    latest_time = dt(latest["openTime"])
    now = datetime.now(timezone.utc)
    age_min = (now - latest_time).total_seconds() / 60

    result = {
        "checked_at": now.isoformat().replace("+00:00", "Z"),
        "data_fetched_at": data["fetchedAt"],
        "latest_m5": latest["openTime"],
        "latest_m5_age_minutes": round(age_min, 1),
        "rule": {
            "direction": "SELL",
            "session_utc": "00:00-06:59",
            "minimum_rr": 2.0,
            "maximum_rr": 2.5,
        },
        "status": "NO TRADE",
    }

    if age_min > 30:
        result["reason"] = "DATA STALE"
    else:
        eng = load_engine()
        analysis = eng.analyze(data)
        result["engine"] = analysis
        hour = latest_time.hour
        if (
            analysis.get("status") == "SETUP FOUND"
            and analysis.get("signal") == "SELL"
            and 0 <= hour < 7
            and 2.0 <= float(analysis.get("rr", 0)) <= 2.5
        ):
            result["status"] = "NEW SIGNAL"
            result["signal"] = analysis

    previous = {}
    if STATE.exists():
        try:
            previous = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            previous = {}

    signal_key = None
    if result["status"] == "NEW SIGNAL":
        s = result["signal"]
        signal_key = f'{s["candle_time"]}|{s["signal"]}|{s["entry"]}|{s["sl"]}|{s["tp"]}'
        if previous.get("last_signal_key") == signal_key:
            result["status"] = "DUPLICATE"
            result["reason"] = "signal already reported"

    state = {
        "last_signal_key": previous.get("last_signal_key"),
        "last_signal_time": previous.get("last_signal_time"),
    }

    if result["status"] == "NEW SIGNAL":
        state["last_signal_key"] = signal_key
        state["last_signal_time"] = result["signal"]["candle_time"]
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        # The signal file is a one-run notification payload.
        # Remove it for both NO TRADE and DUPLICATE so it can never be resent.
        if OUT.exists():
            OUT.unlink()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
