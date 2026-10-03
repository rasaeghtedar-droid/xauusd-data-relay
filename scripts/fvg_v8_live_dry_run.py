#!/usr/bin/env python3
"""Live dry-run relay for the locked V8 Cap4 engine.

Source: Biquote closed XAUUSD M5 candles.
This module only emits a signal record; it never sends orders or calls a broker.
The V8 state machine is replayed over a rolling closed-candle window so pending
FVG state and the one-active-trade rule are preserved.
"""

from __future__ import annotations

import bisect
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fvg_executable_audit_v8 import target as cap4_target
from fvg_only_gold_hunter_backtest import PAD, LOOKBACK, agg, fvg_at, pt

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_PATH = ROOT / "data" / "xauusd_analysis.json"
LIVE_PATH = ROOT / "data" / "xauusd_v8_live.json"

BASE_URL = "https://biquote.io/api/XAUUSD/ohlc"
M5_LIMIT = 1000
FRESHNESS_MINUTES = 30.0


def fetch_closed_m5(limit: int = M5_LIMIT) -> list[dict]:
    query = urllib.parse.urlencode({"interval": "5m", "limit": limit})
    req = urllib.request.Request(
        f"{BASE_URL}?{query}",
        headers={"User-Agent": "XAUUSD-V8-Cap4-Live-DryRun/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} from Biquote")
        payload = json.load(response)

    bars = [b for b in payload.get("bars", []) if b.get("isOpen") is False]
    bars = [
        {
            "openTime": str(b["openTime"]),
            "open": float(b["open"]),
            "high": float(b["high"]),
            "low": float(b["low"]),
            "close": float(b["close"]),
        }
        for b in bars
        if b.get("openTime")
    ]
    bars.sort(key=lambda x: x["openTime"])
    if len(bars) < 100:
        raise RuntimeError(f"Too few closed M5 bars from Biquote: {len(bars)}")
    return bars[-limit:]


def freshness_minutes(open_time: str) -> float:
    now = datetime.now(timezone.utc)
    return (now - pt(open_time)).total_seconds() / 60.0


def run_v8(m5: list[dict]) -> tuple[dict | None, str, dict]:
    m15 = agg(m5, 15)
    m15_times = [pt(x["openTime"]) for x in m15]

    pending: dict[int, dict] = {}
    mid_index = {"BUY": [], "SELL": []}
    invalid_index = {"BUY": [], "SELL": []}
    next_id = 0
    active = None
    latest_time = m5[-1]["openTime"]
    latest_reason = "no new signal on latest closed M5 candle"
    latest_signal = None

    # Preserve the locked V8 sequencing exactly: resolve active, invalidate,
    # confirm, then detect the newly formed FVG on the current candle.
    for i in range(50, len(m5)):
        c = m5[i]
        had_active = active is not None

        if active is not None:
            sl_hit = (c["low"] <= active["sl"]) if active["direction"] == "BUY" else (c["high"] >= active["sl"])
            tp_hit = (c["high"] >= active["tp"]) if active["direction"] == "BUY" else (c["low"] <= active["tp"])
            if sl_hit or tp_hit:
                active = None

        confirmed = []

        for d in ("BUY", "SELL"):
            arr = invalid_index[d]
            if d == "BUY":
                cut = bisect.bisect_right(arr, (-c["low"], 10**18))
            else:
                cut = bisect.bisect_right(arr, (c["high"], 10**18))
            for _, fid in arr[:cut]:
                pending.pop(fid, None)
            invalid_index[d] = arr[cut:]

        for d in ("BUY", "SELL"):
            arr = mid_index[d]
            lo = bisect.bisect_left(arr, (c["low"], -1))
            hi = bisect.bisect_right(arr, (c["high"], 10**18))
            for _, fid in arr[lo:hi]:
                f = pending.get(fid)
                if f is None:
                    continue
                if (d == "BUY" and c["low"] <= f["lo"]) or (d == "SELL" and c["high"] >= f["hi"]):
                    pending.pop(fid, None)
                    continue

                ok = (
                    (d == "BUY" and c["close"] > f["mid"] and c["close"] > c["open"])
                    or (d == "SELL" and c["close"] < f["mid"] and c["close"] < c["open"])
                )
                if not ok:
                    continue

                entry = f["mid"]
                sl = f["lo"] - PAD if d == "BUY" else f["hi"] + PAD
                tp = cap4_target(d, entry, sl, f["ctx"])
                pending.pop(fid, None)
                if tp is None:
                    continue

                rr_raw = (tp - entry) / (entry - sl) if d == "BUY" else (entry - tp) / (sl - entry)
                if rr_raw < 2.0:
                    continue

                confirmed.append(
                    {
                        **f,
                        "confirmation_time": c["openTime"],
                        "entry": round(entry, 3),
                        "sl": round(sl, 3),
                        "tp": round(tp, 3),
                        "rr": round(rr_raw, 2),
                    }
                )

        if c["openTime"] == latest_time:
            if confirmed:
                confirmed.sort(key=lambda x: x["formed_index"])
                candidate = confirmed[0]
                if had_active:
                    latest_reason = "valid V8 confirmation was missed because one active trade already existed"
                else:
                    active = candidate
                    latest_signal = {
                        "engine": "FVG_EXECUTABLE_AUDIT_V8_CAP4",
                        "direction": candidate["direction"],
                        "formation_time": candidate["time"],
                        "confirmation_time": candidate["confirmation_time"],
                        "entry": candidate["entry"],
                        "sl": candidate["sl"],
                        "tp": candidate["tp"],
                        "rr": candidate["rr"],
                        "target_rule": "min(structural target, 4R)",
                        "status": "SIGNAL",
                    }
                    latest_reason = "new V8 Cap4 confirmation on latest closed M5 candle"

        if c["openTime"] != latest_time:
            if active is None and confirmed:
                confirmed.sort(key=lambda x: x["formed_index"])
                active = confirmed[0]

        f = fvg_at(m5, i)
        if f:
            t = pt(f["time"])
            cutoff = t - timedelta(minutes=15)
            mi = bisect.bisect_right(m15_times, cutoff)
            ctx = m15[max(0, mi - LOOKBACK):mi]
            fid = next_id
            next_id += 1
            pending[fid] = {**f, "ctx": ctx, "formed_index": i}
            bisect.insort(mid_index[f["direction"]], (f["mid"], fid))
            if f["direction"] == "BUY":
                bisect.insort(invalid_index["BUY"], (-f["lo"], fid))
            else:
                bisect.insort(invalid_index["SELL"], (f["hi"], fid))

    if active is not None and latest_signal is None:
        latest_reason = latest_reason if latest_reason != "no new signal on latest closed M5 candle" else "one active V8 trade exists; no new signal"

    return latest_signal, latest_reason, {
        "m5_closed": len(m5),
        "m15_built": len(m15),
        "first_m5": m5[0]["openTime"],
        "last_m5": m5[-1]["openTime"],
        "pending_at_end": len(pending),
    }


def merge_into_analysis(payload: dict) -> None:
    if ANALYSIS_PATH.exists():
        data = json.loads(ANALYSIS_PATH.read_text(encoding="utf-8"))
    else:
        data = {}
    data["v8Cap4Live"] = payload
    ANALYSIS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANALYSIS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def main() -> None:
    fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        m5 = fetch_closed_m5()
    except Exception as exc:
        payload = {
            "status": "DATA_NOT_AVAILABLE",
            "engine": "FVG_EXECUTABLE_AUDIT_V8_CAP4",
            "source": "Biquote",
            "fetched_at_utc": fetched_at,
            "reason": str(exc),
        }
        LIVE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        merge_into_analysis(payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    latest = m5[-1]["openTime"]
    age = freshness_minutes(latest)
    if age > FRESHNESS_MINUTES:
        payload = {
            "status": "DATA_STALE",
            "engine": "FVG_EXECUTABLE_AUDIT_V8_CAP4",
            "source": "Biquote",
            "fetched_at_utc": fetched_at,
            "latest_closed_m5": latest,
            "freshness_minutes": round(age, 2),
            "freshness_rule": "<= 30 minutes",
            "reason": "latest closed M5 candle is stale",
        }
    else:
        signal, reason, meta = run_v8(m5)
        payload = {
            "status": "SIGNAL" if signal else "NO TRADE",
            "engine": "FVG_EXECUTABLE_AUDIT_V8_CAP4",
            "source": "Biquote",
            "fetched_at_utc": fetched_at,
            "latest_closed_m5": latest,
            "freshness_minutes": round(age, 2),
            "reason": reason,
            "data": meta,
            "signal": signal,
        }

    LIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIVE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    merge_into_analysis(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
