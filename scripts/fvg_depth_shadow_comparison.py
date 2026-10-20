#!/usr/bin/env python3
"""Same-candle 50% vs 70% FVG depth shadow comparison. No orders, Telegram, or production writes."""
from __future__ import annotations
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
import urllib.request
import fvg_v8_live_dry_run as live

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "fvg_depth_shadow_comparison.json"
CSV_OUT = ROOT / "data" / "fvg_depth_shadow_comparison.csv"
SPREAD = 0.42
DEPTHS = (0.50, 0.70)
RELAY_CACHE = ROOT / "data" / "_relay_xauusd_analysis.json"
PERSISTENT_BARS = ROOT / "data" / "fvg_depth_shadow_candles.json"
MAX_PERSISTENT_BARS = 10000
ORIGINAL_FVG_AT = live.fvg_at

def load_comparison_bars():
    """Merge persistent history with relay and ALWAYS attempt a direct fresh M5 fetch."""
    by_time = {}
    persistence_note = "persistent shadow candle store"
    if PERSISTENT_BARS.exists():
        try:
            saved = json.loads(PERSISTENT_BARS.read_text(encoding="utf-8"))
            for b in saved.get("bars", []):
                if b.get("openTime"):
                    by_time[str(b["openTime"])] = {"openTime": str(b["openTime"]), "open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]), "close": float(b["close"])}
        except (OSError, ValueError, TypeError, KeyError):
            persistence_note = "persistent store unreadable; rebuilt from available sources"
            by_time = {}
    relay_fetched_at = None
    relay_count = 0
    if RELAY_CACHE.exists():
        try:
            obj = json.loads(RELAY_CACHE.read_text(encoding="utf-8"))
            relay_fetched_at = obj.get("fetchedAt")
            raw = obj.get("intervals", {}).get("5m", {}).get("bars", [])
            for b in raw:
                if b.get("openTime") and b.get("isOpen") is False:
                    by_time[str(b["openTime"])] = {"openTime": str(b["openTime"]), "open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]), "close": float(b["close"])}
                    relay_count += 1
        except (OSError, ValueError, TypeError, KeyError):
            pass
    direct_api_count = 0
    direct_api_latest = None
    direct_api_error = None
    try:
        api_bars, api_diag = live.fetch_closed_m5()
        for b in api_bars:
            if b.get("openTime"):
                by_time[str(b["openTime"])] = {"openTime": str(b["openTime"]), "open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]), "close": float(b["close"])}
                direct_api_count += 1
        if api_bars:
            direct_api_latest = max(str(b["openTime"]) for b in api_bars if b.get("openTime"))
    except Exception as exc:
        direct_api_error = f"{type(exc).__name__}: {exc}"[:500]
    bars = sorted(by_time.values(), key=lambda b: b["openTime"])[-MAX_PERSISTENT_BARS:]
    if len(bars) < 100:
        raise RuntimeError(f"Insufficient closed M5 bars for shadow comparison: {len(bars)}")
    PERSISTENT_BARS.parent.mkdir(parents=True, exist_ok=True)
    PERSISTENT_BARS.write_text(json.dumps({"schema_version": 1, "purpose": "isolated FVG 50% vs 70% shadow comparison only", "updated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "bar_count": len(bars), "max_bars": MAX_PERSISTENT_BARS, "bars": bars}, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    diagnostics = {"source": "persistent history + latest relay bars + direct Biquote fetch attempted each run", "relay_fetched_at": relay_fetched_at, "relay_bars_merged_this_run": relay_count, "direct_api_bars_merged_this_run": direct_api_count, "direct_api_latest_closed_open_time": direct_api_latest, "direct_api_error": direct_api_error, "persistent_history_bars": len(bars), "first_persisted_bar": bars[0]["openTime"], "latest_closed_open_time": bars[-1]["openTime"], "persistent_store": str(PERSISTENT_BARS.relative_to(ROOT)), "persistence_note": persistence_note}
    return bars, diagnostics

def fvg_at_depth(depth):
    def patched(candles, i):
        fvg = ORIGINAL_FVG_AT(candles, i)
        if not fvg:
            return fvg
        adjusted = dict(fvg)
        lo, hi = float(adjusted["lo"]), float(adjusted["hi"])
        width = hi - lo
        if width <= 0:
            return None
        # Directional depth measured from the near edge: BUY enters from the top,
        # SELL from the bottom, preserving the existing engine's convention.
        adjusted["mid"] = hi - depth * width if adjusted["direction"] == "BUY" else lo + depth * width
        return adjusted
    return patched

def safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def summarize(depth, signal, reason, meta, result, records, bars):
    rows = []
    for original in records:
        x = dict(original)
        status = str(x.get("status", "UNKNOWN")).upper()
        activated = bool(x.get("activation_time"))
        entry, sl = safe_float(x.get("entry")), safe_float(x.get("sl"))
        direction = x.get("direction")
        risk = entry - sl if direction == "BUY" else sl - entry
        cost_r = SPREAD / risk if activated and risk > 0 else 0.0
        raw = x.get("result_r")
        if status == "TP":
            gross = safe_float(raw, 0.0)
        elif status == "SL":
            gross = safe_float(raw, -1.0)
        else:
            gross = None
        # Ambiguous same-candle TP/SL is not silently called a loss or win.
        # Report both observed net and a conservative lower-bound scenario.
        x.update({
            "depth": depth,
            "entry_activated": activated,
            "spread_assumption_price_units": SPREAD,
            "estimated_spread_cost_r": round(cost_r, 4),
            "estimated_net_r": round(gross - cost_r, 4) if gross is not None and activated else (0.0 if status in ("EXPIRED", "INVALIDATED") else None),
            "conservative_ambiguous_net_r": round(-1.0 - cost_r, 4) if status == "AMBIGUOUS" and activated else None,
            "execution_verified": False,
        })
        rows.append(x)
    tp = sum(x.get("status") == "TP" for x in rows)
    sl_count = sum(x.get("status") == "SL" for x in rows)
    ambiguous = sum(x.get("status") == "AMBIGUOUS" for x in rows)
    activated_resolved = [x for x in rows if x.get("entry_activated") and x.get("status") in ("TP", "SL", "AMBIGUOUS")]
    observed_net = sum(safe_float(x.get("estimated_net_r")) for x in activated_resolved if x.get("estimated_net_r") is not None)
    conservative_net = observed_net + sum(safe_float(x.get("conservative_ambiguous_net_r")) - safe_float(x.get("estimated_net_r")) for x in activated_resolved if x.get("status") == "AMBIGUOUS")
    return {
        "depth": depth,
        "current_signal": signal,
        "current_reason": reason,
        "latest_result": result,
        "replay_meta": meta,
        "trade_records": rows,
        "stats": {
            "records": len(rows),
            "activated_resolved": len(activated_resolved),
            "tp": tp, "sl": sl_count, "ambiguous": ambiguous,
            "expired_unfilled": sum(x.get("status") == "EXPIRED" for x in rows),
            "invalidated_unfilled": sum(x.get("status") == "INVALIDATED" for x in rows),
            "pending_or_active": sum(x.get("status") in ("PENDING", "ACTIVE") for x in rows),
            "win_rate_excluding_ambiguous_percent": round(tp / (tp + sl_count) * 100, 2) if tp + sl_count else None,
            "observed_net_r_excluding_ambiguous": round(observed_net, 4),
            "conservative_net_r_ambiguous_as_full_sl": round(conservative_net, 4),
            "spread_price_units": SPREAD,
        }
    }

def main():
    fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    m5, diagnostics = load_comparison_bars()
    latest = m5[-1]["openTime"]
    age = live.freshness_minutes(latest)
    freshness_status = "FRESH" if age <= live.FRESHNESS_MINUTES else "STALE_CURRENT_MARKET_DATA"
    # This is a historical comparison, so still replay valid closed candles when
    # the market is closed or the latest candle is old. Freshness is reported,
    # not used to discard a useful historical sample.
    results = {}
    all_rows = []
    for depth in DEPTHS:
        live.fvg_at = fvg_at_depth(depth)
        signal, reason, meta, result, history = live.run_v8(m5)
        summary = summarize(depth, signal, reason, meta, result, history, m5)
        results[f"{int(depth * 100)}_percent"] = summary
        all_rows.extend(summary["trade_records"])
    live.fvg_at = ORIGINAL_FVG_AT
    s50 = results["50_percent"]["stats"]
    s70 = results["70_percent"]["stats"]
    payload = {
        "status": "OK",
        "test_mode": "ISOLATED_SAME_CANDLE_DEPTH_COMPARISON",
        "fetched_at_utc": fetched_at,
        "source": diagnostics.get("source", "Biquote closed XAUUSD M5 OHLC"),
        "latest_closed_m5": live.m5_close_time(latest).isoformat().replace("+00:00", "Z"),
        "freshness_minutes": round(age, 2),
        "current_data_freshness": freshness_status,
        "api_diagnostics": diagnostics,
        "data_window": {"m5_closed": len(m5), "first_m5_open_time": m5[0]["openTime"], "last_m5_open_time": latest, "target_window": f"persistent history up to {MAX_PERSISTENT_BARS} closed M5 candles", "persistent_history_bars": len(m5)},
        "assumptions": {
            "depths_compared": [0.50, 0.70],
            "spread_price_units": SPREAD,
            "spread_application": "estimated once per activated trade; no charge for unfilled pending setups",
            "ambiguous_bar_handling": "reported separately; conservative lower-bound metric treats activated ambiguous TP/SL bar as full -1R plus estimated spread",
            "entry_confirmation_and_activation": "existing V8 engine sequencing replayed independently at each depth over exactly the same M5 candle array",
            "limitations": "OHLC only, no historical Bid/Ask or tick path; spread and fills are estimates, not broker-execution validation",
            "real_orders_sent": False, "telegram_sent": False, "production_dashboard_modified": False,
        },
        "comparison": {
            "same_candle_window": True,
            "delta_observed_net_r_70_minus_50": round(s70["observed_net_r_excluding_ambiguous"] - s50["observed_net_r_excluding_ambiguous"], 4),
            "delta_conservative_net_r_70_minus_50": round(s70["conservative_net_r_ambiguous_as_full_sl"] - s50["conservative_net_r_ambiguous_as_full_sl"], 4),
            "note": "Compare sample counts and ambiguous trades alongside R totals; the better result on a short rolling window is not proof of durable superiority."
        },
        "depths": results,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    columns = ["depth", "signal_key", "direction", "formation_time", "confirmation_time", "entry", "sl", "tp", "rr", "status", "result_r", "activation_time", "exit_time", "entry_activated", "estimated_spread_cost_r", "estimated_net_r", "conservative_ambiguous_net_r", "execution_verified"]
    with CSV_OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    print(json.dumps({
        "status": payload["status"], "latest_closed_m5": payload["latest_closed_m5"],
        "data_window": payload["data_window"], "comparison": payload["comparison"],
        "50_percent": s50, "70_percent": s70,
        "artifacts": [OUT.name, CSV_OUT.name],
        "real_orders_sent": False, "telegram_sent": False, "production_dashboard_modified": False
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
