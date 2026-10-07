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
import sys
import time

# Make sibling modules in scripts/ importable whether this file is run
# directly (python scripts/...) or imported by a workflow test.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from fvg_executable_audit_v8 import target as cap4_target
from fvg_only_gold_hunter_backtest import PAD, LOOKBACK, agg, fvg_at, pt

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_PATH = ROOT / "data" / "xauusd_analysis.json"
LIVE_PATH = ROOT / "data" / "xauusd_v8_live.json"
HISTORY_PATH = ROOT / "data" / "xauusd_v8_history.json"

BASE_URL = "https://biquote.io/api/XAUUSD/ohlc"
M5_LIMIT = 1000
FRESHNESS_MINUTES = 30.0
# A confirmed signal that has not touched Entry expires after this many
# minutes. This prevents a setup from remaining actionable indefinitely while
# price keeps moving away from the FVG.
PENDING_ENTRY_MAX_MINUTES = 60
# Pending-entry technical invalidation:
# BUY is invalidated by a CLOSED M5 candle below the original FVG low.
# SELL is invalidated by a CLOSED M5 candle above the original FVG high.
# A wick alone does not cancel the setup; the invalidation requires a close.


def fetch_closed_m5(limit: int = M5_LIMIT) -> tuple[list[dict], dict]:
    query = urllib.parse.urlencode({"interval": "5m", "limit": limit})
    request_started = datetime.now(timezone.utc)
    started_perf = time.perf_counter()
    req = urllib.request.Request(
        f"{BASE_URL}?{query}",
        headers={"User-Agent": "XAUUSD-V8-Cap4-Live-DryRun/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} from Biquote")
        payload = json.load(response)

    response_received = datetime.now(timezone.utc)
    api_latency_seconds = round(time.perf_counter() - started_perf, 3)
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
    diagnostics = {
        "request_started_utc": request_started.isoformat().replace("+00:00", "Z"),
        "response_received_utc": response_received.isoformat().replace("+00:00", "Z"),
        "api_latency_seconds": api_latency_seconds,
        "api_bar_count": len(payload.get("bars", [])),
        "closed_bar_count": len(bars),
        "latest_closed_open_time": bars[-1]["openTime"],
        "latest_closed_close_time": m5_close_time(bars[-1]["openTime"]).isoformat().replace("+00:00", "Z"),
    }
    return bars[-limit:], diagnostics


def m5_close_time(open_time: str) -> datetime:
    # Biquote labels each M5 candle by its opening time. A candle opened at
    # 15:25 UTC is closed at 15:30 UTC, so freshness must be measured from
    # the close time, not from the candle label/open time.
    return pt(open_time) + timedelta(minutes=5)


def freshness_minutes(open_time: str) -> float:
    now = datetime.now(timezone.utc)
    return (now - m5_close_time(open_time)).total_seconds() / 60.0


def timeframe_snapshot(m5: list[dict]) -> dict:
    def snap(bars: list[dict]) -> dict:
        if not bars:
            return {"status": "DATA_NOT_AVAILABLE"}

        c = bars[-1]
        highs = []
        lows = []
        # Confirmed 2-left / 2-right swings. This is display-only and does
        # not participate in V8 entry decisions.
        for i in range(2, len(bars) - 2):
            h = bars[i]["high"]
            l = bars[i]["low"]
            if h > max(bars[i-2]["high"], bars[i-1]["high"], bars[i+1]["high"], bars[i+2]["high"]):
                highs.append(h)
            if l < min(bars[i-2]["low"], bars[i-1]["low"], bars[i+1]["low"], bars[i+2]["low"]):
                lows.append(l)

        structure = "NEUTRAL"
        if len(highs) >= 2 and len(lows) >= 2:
            hh = highs[-1] > highs[-2]
            hl = lows[-1] > lows[-2]
            lh = highs[-1] < highs[-2]
            ll = lows[-1] < lows[-2]
            if hh and hl:
                structure = "BULLISH"
            elif lh and ll:
                structure = "BEARISH"

        return {
            "openTime": c["openTime"],
            "open": c["open"],
            "high": c["high"],
            "low": c["low"],
            "close": c["close"],
            "direction": "BULLISH" if c["close"] >= c["open"] else "BEARISH",
            "structure": structure,
            "bias": structure,
        }

    return {
        "H1": snap(agg(m5, 60)),
        "M15": snap(agg(m5, 15)),
        "M5": snap(m5),
    }


def run_v8(m5: list[dict]) -> tuple[dict | None, str, dict, dict | None]:
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
    latest_result = None
    trade_history = []
    active_history = None

    # Preserve the locked V8 sequencing exactly: resolve active, invalidate,
    # confirm, then detect the newly formed FVG on the current candle.
    for i in range(50, len(m5)):
        c = m5[i]
        had_active = active is not None

        if active is not None:
            # A V8 signal is NOT a trade until price actually touches Entry.
            # Before Entry, TP/SL must never resolve the signal. This prevents
            # a one-way move from Entry -> TP from being counted as a TP when
            # the real signal was never executable.
            if not active.get("entry_activated", False):
                # Technical invalidation has priority over the 60-minute timer.
                # We use the original FVG boundary and a CLOSED M5 candle:
                # BUY -> close below FVG low = bullish setup invalid.
                # SELL -> close above FVG high = bearish setup invalid.
                # A wick through the boundary alone does not cancel it.
                technical_invalid = (
                    c["close"] < active["zone_lo"]
                    if active["direction"] == "BUY"
                    else c["close"] > active["zone_hi"]
                )
                if technical_invalid:
                    latest_result = {
                        "status": "INVALIDATED",
                        "icon": "⚠️",
                        "signal_key": f'{active["direction"]}|{active["confirmation_time"]}',
                        "direction": active["direction"],
                        "entry": active["entry"],
                        "sl": active["sl"],
                        "tp": active["tp"],
                        "rr": active["rr"],
                        "result_r": 0.0,
                        "activation_time": None,
                        "exit_time": c["openTime"],
                        "expiry_reason": "technical invalidation: closed M5 candle crossed the FVG boundary",
                    }
                    if active_history is not None:
                        active_history.update(latest_result)
                        trade_history.append(dict(active_history))
                    else:
                        trade_history.append(dict(latest_result))
                    active = None
                    active_history = None
                    continue
                # Pending-entry validity window: 60 minutes from confirmation.
                # If Entry is not touched inside this window, the setup expires
                # and is no longer actionable.
                confirmation_dt = pt(active["confirmation_time"])
                current_dt = pt(c["openTime"])
                pending_age_minutes = (current_dt - confirmation_dt).total_seconds() / 60.0
                if active is None:
                    continue
                if pending_age_minutes >= PENDING_ENTRY_MAX_MINUTES:
                    latest_result = {
                        "status": "EXPIRED",
                        "icon": "⌛",
                        "signal_key": f'{active["direction"]}|{active["confirmation_time"]}',
                        "direction": active["direction"],
                        "entry": active["entry"],
                        "sl": active["sl"],
                        "tp": active["tp"],
                        "rr": active["rr"],
                        "result_r": 0.0,
                        "activation_time": None,
                        "exit_time": c["openTime"],
                        "expiry_reason": "Entry not touched within 60 minutes",
                    }
                    if active_history is not None:
                        active_history.update(latest_result)
                        trade_history.append(dict(active_history))
                    else:
                        trade_history.append(dict(latest_result))
                    active = None
                    active_history = None
                else:
                    entry_hit = (
                        c["low"] <= active["entry"]
                        if active["direction"] == "BUY"
                        else c["high"] >= active["entry"]
                    )
                    if entry_hit:
                        active["entry_activated"] = True
                        active["activation_time"] = c["openTime"]
                        if active_history is not None:
                            active_history["status"] = "ACTIVE"
                            active_history["activation_time"] = c["openTime"]
                    # If Entry was not touched, this signal remains pending and
                    # the one-active-trade rule stays in force.
                    if not active.get("entry_activated", False):
                        continue
            sl_hit = (c["low"] <= active["sl"]) if active["direction"] == "BUY" else (c["high"] >= active["sl"])
            tp_hit = (c["high"] >= active["tp"]) if active["direction"] == "BUY" else (c["low"] <= active["tp"])

            if sl_hit or tp_hit:
                if sl_hit and tp_hit:
                    outcome = "AMBIGUOUS"
                    result_icon = "⚠️"
                    result_r = 0.0
                elif tp_hit:
                    outcome = "TP"
                    result_icon = "✅"
                    result_r = active["rr"]
                else:
                    outcome = "SL"
                    result_icon = "❌"
                    result_r = -1.0

                latest_result = {
                    "status": outcome,
                    "icon": result_icon,
                    "signal_key": f'{active["direction"]}|{active["confirmation_time"]}',
                    "direction": active["direction"],
                    "entry": active["entry"],
                    "sl": active["sl"],
                    "tp": active["tp"],
                    "rr": active["rr"],
                    "result_r": round(result_r, 2),
                    "activation_time": active.get("activation_time"),
                    "exit_time": c["openTime"],
                }

                if active_history is not None:
                    active_history.update(latest_result)
                    trade_history.append(dict(active_history))
                else:
                    trade_history.append(dict(latest_result))

                active = None
                active_history = None

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
                        "zone_lo": f["lo"],
                        "zone_hi": f["hi"],
                    }
                )

        if c["openTime"] == latest_time:
            if confirmed:
                confirmed.sort(key=lambda x: x["formed_index"])
                candidate = confirmed[0]
                if had_active:
                    latest_reason = "valid V8 confirmation was missed because one active trade already existed"
                else:
                    candidate["entry_activated"] = False
                    candidate["activation_time"] = None
                    candidate["pending_expiry_minutes"] = PENDING_ENTRY_MAX_MINUTES
                    active = candidate
                    active_history = {
                        "signal_key": f'{candidate["direction"]}|{candidate["confirmation_time"]}',
                        "direction": candidate["direction"],
                        "confirmation_time": candidate["confirmation_time"],
                        "formation_time": candidate["time"],
                        "entry": candidate["entry"],
                        "sl": candidate["sl"],
                        "tp": candidate["tp"],
                        "rr": candidate["rr"],
                        "status": "PENDING",
                        "result_r": None,
                        "activation_time": None,
                        "pending_expiry_minutes": PENDING_ENTRY_MAX_MINUTES,
                        "exit_time": None,
                    }
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
                        "status": "PENDING_ENTRY",
                    }
                    latest_reason = "new V8 Cap4 confirmation on latest closed M5 candle"

        if c["openTime"] != latest_time:
            if active is None and confirmed:
                confirmed.sort(key=lambda x: x["formed_index"])
                active = confirmed[0]
                active["entry_activated"] = False
                active["activation_time"] = None
                active["pending_expiry_minutes"] = PENDING_ENTRY_MAX_MINUTES
                active_history = {
                    "signal_key": f'{active["direction"]}|{active["confirmation_time"]}',
                    "direction": active["direction"],
                    "confirmation_time": active["confirmation_time"],
                    "formation_time": active["time"],
                    "entry": active["entry"],
                    "sl": active["sl"],
                    "tp": active["tp"],
                    "rr": active["rr"],
                    "status": "PENDING",
                    "result_r": None,
                    "activation_time": None,
                    "pending_expiry_minutes": PENDING_ENTRY_MAX_MINUTES,
                    "pending_invalidation_rule": "closed M5 candle beyond original FVG boundary",
                    "exit_time": None,
                }

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

    # The workflow starts from a clean checkout on every run. Therefore the
    # live decision must be reconstructed from the rolling closed-candle window,
    # not from a previous workspace file. If a V8 trade is still active at the
    # end of the replay, expose that active trade as the current signal.
    if active is not None:
        entry_activated = bool(active.get("entry_activated", False))
        latest_signal = {
            "engine": "FVG_EXECUTABLE_AUDIT_V8_CAP4",
            "direction": active["direction"],
            "formation_time": active.get("formation_time", active.get("time")),
            "confirmation_time": active["confirmation_time"],
            "entry": active["entry"],
            "sl": active["sl"],
            "tp": active["tp"],
            "rr": active["rr"],
            "target_rule": active.get("target_rule", "V8_CAP4"),
            "status": "ACTIVE" if entry_activated else "PENDING_ENTRY",
            "activation_time": active.get("activation_time"),
            "pending_expiry_minutes": PENDING_ENTRY_MAX_MINUTES,
            "pending_invalidation_rule": "closed M5 candle beyond original FVG boundary",
        }
        latest_reason = (
            "active V8 trade waiting for Entry touch"
            if not entry_activated
            else "active V8 trade reconstructed from closed M5 replay"
        )
        if active_history is not None:
            trade_history.append(dict(active_history))

    return latest_signal, latest_reason, {
        "m5_closed": len(m5),
        "m15_built": len(m15),
        "first_m5": m5[0]["openTime"],
        "last_m5": m5[-1]["openTime"],
        "pending_at_end": len(pending),
    }, latest_result, trade_history


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
        m5, api_diagnostics = fetch_closed_m5()
    except Exception as exc:
        payload = {
            "status": "DATA_NOT_AVAILABLE",
            "engine": "FVG_EXECUTABLE_AUDIT_V8_CAP4",
            "source": "Biquote",
            "fetched_at_utc": fetched_at,
            "reason": str(exc),
            "api_diagnostics": {"request_started_utc": fetched_at},
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
            "latest_closed_m5": m5_close_time(latest).isoformat().replace("+00:00", "Z"),
            "latest_closed_m5_open_time": latest,
            "freshness_minutes": round(age, 2),
            "freshness_rule": "<= 30 minutes",
            "reason": "latest closed M5 candle is stale",
            "api_diagnostics": api_diagnostics,
        }
    else:
        signal, reason, meta, result, replay_history = run_v8(m5)

        existing_history = []
        if HISTORY_PATH.exists():
            try:
                existing_history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
                if not isinstance(existing_history, list):
                    existing_history = []
            except (OSError, json.JSONDecodeError):
                existing_history = []

        history_by_key = {
            str(item.get("signal_key")): dict(item)
            for item in existing_history
            if item.get("signal_key")
        }
        # IMPORTANT: replay_history is only allowed to UPDATE a trade
        # that was already recorded as a live signal. It must never CREATE a
        # brand-new history record. Otherwise a later rolling replay can make
        # an old signal appear on the dashboard hours after it actually formed.
        for item in replay_history:
            key = item.get("signal_key")
            if not key or key not in history_by_key:
                continue
            current = history_by_key[key]
            current.update(item)
            history_by_key[key] = current

        # Only the signal confirmed on the CURRENT latest closed M5 candle is
        # allowed to create a new history record. A reconstructed ACTIVE trade
        # from an older candle must already exist in history to be updated.
        live_signal_key = None
        if signal and signal.get("confirmation_time") == latest:
            live_signal_key = f'{signal["direction"]}|{signal["confirmation_time"]}'
            if live_signal_key not in history_by_key:
                history_by_key[live_signal_key] = {
                    "signal_key": live_signal_key,
                    "direction": signal["direction"],
                    "confirmation_time": signal["confirmation_time"],
                    "formation_time": signal.get("formation_time"),
                    "entry": signal["entry"],
                    "sl": signal["sl"],
                    "tp": signal["tp"],
                    "rr": signal["rr"],
                    "status": "PENDING",
                    "result_r": None,
                    "activation_time": signal.get("activation_time"),
                    "pending_expiry_minutes": PENDING_ENTRY_MAX_MINUTES,
                    "exit_time": None,
                }

        history = sorted(
            history_by_key.values(),
            key=lambda x: str(x.get("confirmation_time", "")),
            reverse=True,
        )

        stats = {
            "total": len(history),
            "tp": sum(1 for x in history if x.get("status") == "TP"),
            "sl": sum(1 for x in history if x.get("status") == "SL"),
            "ambiguous": sum(1 for x in history if x.get("status") == "AMBIGUOUS"),
            "pending": sum(1 for x in history if x.get("status") in ("PENDING", "ACTIVE")),
        }
        stats["resolved"] = stats["tp"] + stats["sl"] + stats["ambiguous"]
        stats["win_rate_percent"] = round((stats["tp"] / stats["resolved"]) * 100, 2) if stats["resolved"] else None

        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_PATH.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Prevent the same confirmation candle from being emitted repeatedly.
        # A later run may still reconstruct the same trade from replay, but only
        # the first observation of the CURRENT latest M5 candle is actionable.
        previous = None
        if LIVE_PATH.exists():
            try:
                previous = json.loads(LIVE_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous = None

        signal_key = None
        if signal:
            signal_key = f'{signal["direction"]}|{signal["confirmation_time"]}'
        previous_key = (previous or {}).get("signal_key")

        is_new_signal = bool(
            signal
            and signal.get("confirmation_time") == latest
            and signal_key != previous_key
        )
        if signal and not is_new_signal:
            reason = "same V8 confirmation already emitted; keeping the active signal"
            signal = None

        # Keep the last actionable signal visible until that trade resolves at TP/SL.
        # An older replay reconstruction must NOT be presented as a fresh signal.
        retained_signal = None
        if not signal and previous:
            prev_signal = previous.get("signal")
            prev_result = previous.get("result")
            prev_status = previous.get("status")
            if prev_status == "SIGNAL" and prev_signal and not prev_result:
                retained_signal = prev_signal
                signal = retained_signal
                signal_key = previous.get("signal_key")
                reason = "active V8 signal retained; waiting for TP/SL or a new setup"

        payload = {
            "status": "SIGNAL" if (is_new_signal or retained_signal) else "NO TRADE",
            "engine": "FVG_EXECUTABLE_AUDIT_V8_CAP4",
            "source": "Biquote",
            "fetched_at_utc": fetched_at,
            "latest_closed_m5": m5_close_time(latest).isoformat().replace("+00:00", "Z"),
            "latest_closed_m5_open_time": latest,
            "latest_closed_m5_close_time": m5_close_time(latest).isoformat().replace("+00:00", "Z"),
            "freshness_minutes": round(age, 2),
            "reason": reason,
            "api_diagnostics": api_diagnostics,
            "data": meta,
            "timeframes": timeframe_snapshot(m5),
            "signal_key": signal_key,
            "signal": signal,
            "result": result,
            "history": history,
            "history_stats": stats,
        }

    LIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIVE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    merge_into_analysis(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
