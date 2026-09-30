#!/usr/bin/env python3
"""Event-driven Combined Gold Hunter backtest: Liquidity + FVG, no look-ahead."""
from __future__ import annotations
import csv, io, json, os, urllib.request
from datetime import datetime, timedelta
from pathlib import Path

URL = os.getenv(
    "COMBINED_SOURCE_URL",
    "https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/sample-2026-07-31/XAUUSD_5m.csv",
)
MIN_RR, MAX_RR = 2.0, 2.5
PAD = 0.5
LOOKBACK = 30
SWING = 3
TOL = 1.5
FVG_SWEEP_LOOKBACK = 6
FVG_ATR_LOOKBACK = 14

def pt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def body(c):
    return abs(c["close"] - c["open"])

def rng(c):
    return c["high"] - c["low"]

def swings(bs, side, lb=SWING):
    if len(bs) < 2 * lb + 1:
        return []
    out = []
    for i in range(lb, len(bs) - lb):
        v = bs[i][side]
        vals = [x[side] for x in bs[i-lb:i+lb+1]]
        if (v >= max(vals) if side == "high" else v <= min(vals)):
            out.append(v)
    return out

def equals(bs, side):
    vals = [b[side] for b in bs]
    out = []
    for i, v in enumerate(vals):
        near = [x for j, x in enumerate(vals) if j != i and abs(x-v) <= TOL]
        if near:
            out.append((v + sum(near)) / (len(near) + 1))
    return out

def uniq(xs):
    out = []
    for x in sorted(xs):
        if not out or abs(x - out[-1]) > TOL:
            out.append(x)
    return out

def quality(c, level, direction):
    r = rng(c)
    if r <= 0:
        return False
    b = body(c)
    pen = (c["high"] - level) if direction == "SELL" else (level - c["low"])
    if pen <= 0 or pen < 0.10 * r:
        return False
    if direction == "SELL":
        wick = c["high"] - max(c["open"], c["close"])
        pos = (c["close"] - c["low"]) / r
        return wick >= max(0.5*b, 0.15*r) and pos <= 0.60
    wick = min(c["open"], c["close"]) - c["low"]
    pos = (c["close"] - c["low"]) / r
    return wick >= max(0.5*b, 0.15*r) and pos >= 0.40

def m5_conf(bs, direction):
    if len(bs) < 4:
        return None
    a, b, c, z = bs[-4], bs[-3], bs[-2], bs[-1]
    if direction == "BUY" and z["close"] > max(a["high"], b["high"], c["high"]):
        return "structure break"
    if direction == "SELL" and z["close"] < min(a["low"], b["low"], c["low"]):
        return "structure break"
    recent = [body(x) for x in bs[-8:-1] if rng(x) > 0]
    avg = sum(recent) / len(recent) if recent else 0
    if avg and body(z) >= 1.5 * avg:
        if direction == "BUY" and z["close"] > z["open"]:
            return "displacement"
        if direction == "SELL" and z["close"] < z["open"]:
            return "displacement"
    if direction == "BUY" and z["close"] > z["open"] and z["close"] > c["high"]:
        return "continuation"
    if direction == "SELL" and z["close"] < z["open"] and z["close"] < c["low"]:
        return "continuation"
    return None

def target(direction, entry, sl, ctx):
    if direction == "BUY":
        levels = uniq(swings(ctx, "high") + equals(ctx, "high"))
        vals = [x for x in sorted(x for x in levels if x > entry)
                if MIN_RR <= (x-entry)/(entry-sl) <= MAX_RR]
        return vals[0] if vals else None
    levels = uniq(swings(ctx, "low") + equals(ctx, "low"))
    vals = [x for x in sorted((x for x in levels if x < entry), reverse=True)
            if MIN_RR <= (entry-x)/(sl-entry) <= MAX_RR]
    return vals[0] if vals else None

def closed_m15_context(m15, t):
    return [x for x in m15 if pt(x["openTime"]) <= t - timedelta(minutes=15)][-LOOKBACK:]

def liquidity_setup(m5, m15, i):
    t = pt(m5[i]["openTime"])
    ctx = closed_m15_context(m15, t)
    if len(ctx) < 10:
        return None
    highs = uniq(swings(ctx, "high") + equals(ctx, "high"))
    lows = uniq(swings(ctx, "low") + equals(ctx, "low"))
    c = m5[i]
    found = None
    for lv in sorted(highs, reverse=True):
        if c["high"] > lv and c["close"] < lv and quality(c, lv, "SELL"):
            found = ("SELL", lv, "buy-side sweep")
            break
    if not found:
        for lv in sorted(lows):
            if c["low"] < lv and c["close"] > lv and quality(c, lv, "BUY"):
                found = ("BUY", lv, "sell-side sweep")
                break
    if not found:
        return None
    direction, lv, typ = found
    conf = m5_conf(m5[:i+1], direction)
    if not conf:
        return None
    entry = c["close"]
    sl = max(c["high"], lv) + PAD if direction == "SELL" else min(c["low"], lv) - PAD
    tp = target(direction, entry, sl, ctx)
    if tp is None:
        return None
    rr = (entry-tp)/(sl-entry) if direction == "SELL" else (tp-entry)/(entry-sl)
    return {
        "engine": "LIQUIDITY", "direction": direction, "entry": entry,
        "sl": sl, "tp": tp, "rr": rr, "trigger": conf,
        "time": c["openTime"], "location": typ,
    }

def atr(bs, i):
    vals = [rng(x) for x in bs[max(0, i-FVG_ATR_LOOKBACK):i] if rng(x) > 0]
    return sum(vals)/len(vals) if vals else None

def fvg_at_formation(bs, i):
    """Detect an FVG using only candles i-2, i-1, i; i is the just-closed candle."""
    if i < 9:
        return None
    recent = [body(x) for x in bs[i-8:i-1] if rng(x) > 0]
    if not recent:
        return None
    middle = bs[i-1]
    if body(middle) < 1.25 * sum(recent)/len(recent) or body(middle)/rng(middle) < 0.55:
        return None
    first, third = bs[i-2], bs[i]
    if first["high"] < third["low"]:
        lo, hi, direction = first["high"], third["low"], "BUY"
    elif first["low"] > third["high"]:
        lo, hi, direction = third["high"], first["low"], "SELL"
    else:
        return None
    aatr = atr(bs, i)
    if not aatr or hi-lo < 0.10 * aatr:
        return None
    return {
        "lo": lo, "hi": hi, "mid": (lo+hi)/2, "atr": aatr,
        "direction": direction, "time": third["openTime"],
        "formation_index": i,
    }

def near_liq(f, m15):
    ctx = closed_m15_context(m15, pt(f["time"]))
    if len(ctx) < 10:
        return False
    side = "low" if f["direction"] == "BUY" else "high"
    levels = uniq(swings(ctx, side) + equals(ctx, side))
    return any(abs(f["mid"] - x) <= f["atr"] for x in levels)

def recent_sweep(bs, i, direction):
    for j in range(max(5, i-FVG_SWEEP_LOOKBACK), i):
        prior = bs[max(0, j-30):j]
        c = bs[j]
        highs = uniq(swings(prior, "high") + equals(prior, "high"))
        lows = uniq(swings(prior, "low") + equals(prior, "low"))
        levels = lows if direction == "BUY" else highs
        for lv in levels:
            if direction == "BUY" and c["low"] < lv and c["close"] > lv and quality(c, lv, direction):
                return True
            if direction == "SELL" and c["high"] > lv and c["close"] < lv and quality(c, lv, direction):
                return True
    return False

def fvg_is_contextual(f, m5, m15):
    return near_liq(f, m15) or recent_sweep(m5, f["formation_index"], f["direction"])

def fvg_signal_on_candle(f, c, m15, current_time):
    """Evaluate an already-formed FVG using only the current closed candle."""
    if c["low"] > f["mid"] or c["high"] < f["mid"]:
        return None
    direction = f["direction"]
    ok = (
        direction == "BUY" and c["close"] > f["mid"] and c["close"] > c["open"]
    ) or (
        direction == "SELL" and c["close"] < f["mid"] and c["close"] < c["open"]
    )
    if not ok:
        return None
    ctx = closed_m15_context(m15, current_time)
    if len(ctx) < 10:
        return None
    entry = f["mid"]
    sl = f["lo"] - PAD if direction == "BUY" else f["hi"] + PAD
    tp = target(direction, entry, sl, ctx)
    if tp is None:
        return None
    rr = (tp-entry)/(entry-sl) if direction == "BUY" else (entry-tp)/(sl-entry)
    if not (MIN_RR <= rr <= MAX_RR):
        return None
    return {
        "engine": "FVG", "direction": direction, "entry": entry,
        "sl": sl, "tp": tp, "rr": rr,
        "trigger": "FVG return + directional confirmation",
        "time": c["openTime"], "location": "FVG+LIQUIDITY",
        "fvg_formation_time": f["time"],
    }

def load():
    raw = urllib.request.urlopen(URL, timeout=60).read().decode()
    rows = []
    for z in csv.DictReader(io.StringIO(raw)):
        t = z["datetime"]
        t = t if t.endswith("+00:00") else t + "+00:00"
        rows.append({
            "openTime": t, "open": float(z["open"]), "high": float(z["high"]),
            "low": float(z["low"]), "close": float(z["close"]),
        })
    return sorted(rows, key=lambda x: x["openTime"])

def agg(bs, mins):
    out, cur = [], None
    for c in bs:
        dt = pt(c["openTime"])
        k = dt.replace(minute=(dt.minute//mins)*mins, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
        if not cur or cur["openTime"] != k:
            if cur: out.append(cur)
            cur = {"openTime": k, "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"]}
        else:
            cur["high"] = max(cur["high"], c["high"])
            cur["low"] = min(cur["low"], c["low"])
            cur["close"] = c["close"]
    if cur: out.append(cur)
    return out

def outcome(s, m5, start):
    for k in range(start+1, len(m5)):
        b = m5[k]
        sl = b["low"] <= s["sl"] if s["direction"] == "BUY" else b["high"] >= s["sl"]
        tp = b["high"] >= s["tp"] if s["direction"] == "BUY" else b["low"] <= s["tp"]
        if sl and tp: return "AMBIGUOUS"
        if tp: return "TP"
        if sl: return "SL"
    return "OPEN_AT_DATA_END"

def main():
    raw_m5 = load()
    start_s, end_s = os.getenv("COMBINED_START_UTC"), os.getenv("COMBINED_END_UTC")
    start_dt, end_dt = (pt(start_s) if start_s else None), (pt(end_s) if end_s else None)
    m15 = agg(raw_m5, 15)
    # Full source is retained for warmup and future outcome evaluation.
    signals, active_fvgs = [], []
    active_until = None
    counters = {"fvg_formed": 0, "fvg_contextual": 0, "fvg_invalidated": 0, "fvg_retests_without_confirmation": 0}
    i = 50
    while i < len(raw_m5):
        c, now = raw_m5[i], pt(raw_m5[i]["openTime"])
        if end_dt and now > end_dt:
            break

        # A trade opened earlier blocks new setups until its first terminal candle.
        if active_until and now <= active_until:
            i += 1
            continue

        # First, manage already-formed FVGs. No future scan is allowed.
        fvg_candidates = []
        for f in list(active_fvgs):
            if now <= pt(f["time"]):
                continue
            if f["direction"] == "BUY" and c["low"] <= f["lo"]:
                active_fvgs.remove(f); counters["fvg_invalidated"] += 1; continue
            if f["direction"] == "SELL" and c["high"] >= f["hi"]:
                active_fvgs.remove(f); counters["fvg_invalidated"] += 1; continue
            if c["low"] <= f["mid"] <= c["high"]:
                sig = fvg_signal_on_candle(f, c, m15, now)
                if sig:
                    fvg_candidates.append(sig)
                    active_fvgs.remove(f)
                else:
                    counters["fvg_retests_without_confirmation"] += 1

        # Then detect a newly completed FVG on the current candle. It cannot signal
        # on the same candle because a return requires a later closed candle.
        f = fvg_at_formation(raw_m5, i)
        if f:
            counters["fvg_formed"] += 1
            if fvg_is_contextual(f, raw_m5, m15):
                active_fvgs.append(f)
                counters["fvg_contextual"] += 1

        l = liquidity_setup(raw_m5, m15, i)
        f_sig = fvg_candidates[0] if fvg_candidates else None
        chosen = None
        if l and f_sig:
            # One signal on the same closed candle: mark confluence.
            chosen = {**l, "engine": "CONFLUENCE", "confluence_with": "FVG"}
        else:
            chosen = l or f_sig

        if chosen:
            # Only count signals inside the requested evaluation window.
            if (start_dt is None or now >= start_dt) and (end_dt is None or now <= end_dt):
                chosen = {
                    **chosen,
                    "rr": round(chosen["rr"], 2),
                    "entry": round(chosen["entry"], 3),
                    "sl": round(chosen["sl"], 3),
                    "tp": round(chosen["tp"], 3),
                }
                out = outcome(chosen, raw_m5, i)
                chosen["outcome"] = out
                signals.append(chosen)
                if out in ("TP", "SL", "AMBIGUOUS"):
                    for k in range(i+1, len(raw_m5)):
                        b = raw_m5[k]
                        sl_hit = b["low"] <= chosen["sl"] if chosen["direction"] == "BUY" else b["high"] >= chosen["sl"]
                        tp_hit = b["high"] >= chosen["tp"] if chosen["direction"] == "BUY" else b["low"] <= chosen["tp"]
                        if sl_hit or tp_hit:
                            active_until = pt(b["openTime"])
                            break
            else:
                # A pre-OOS signal is intentionally ignored for performance stats,
                # but it still blocks overlapping trades only if it actually exists
                # in the evaluation engine. For clean OOS stats, do not block on it.
                pass
        i += 1

    w = sum(x["outcome"] == "TP" for x in signals)
    sln = sum(x["outcome"] == "SL" for x in signals)
    amb = sum(x["outcome"] == "AMBIGUOUS" for x in signals)
    net = sum(x["rr"] if x["outcome"] == "TP" else -1 if x["outcome"] == "SL" else 0 for x in signals)
    by = {}
    for e in ("LIQUIDITY", "FVG", "CONFLUENCE"):
        ss = [x for x in signals if x["engine"] == e]
        ww = sum(x["outcome"] == "TP" for x in ss)
        ll = sum(x["outcome"] == "SL" for x in ss)
        by[e] = {
            "signals": len(ss), "tp": ww, "sl": ll,
            "ambiguous": sum(x["outcome"] == "AMBIGUOUS" for x in ss),
            "win_rate": round(100*ww/(ww+ll), 2) if ww+ll else None,
            "net_r": round(sum(x["rr"] if x["outcome"] == "TP" else -1 if x["outcome"] == "SL" else 0 for x in ss), 2),
        }
    result = {
        "status": "COMPLETED_EVENT_DRIVEN_NO_LOOKAHEAD",
        "source": URL,
        "validation_start_utc": start_s, "validation_end_utc": end_s,
        "data": {
            "m5_total_source": len(raw_m5), "m15_total_source": len(m15),
            "first_m5": raw_m5[0]["openTime"], "last_m5": raw_m5[-1]["openTime"],
        },
        "overall": {
            "signals": len(signals), "tp": w, "sl": sln, "ambiguous": amb,
            "win_rate": round(100*w/(w+sln), 2) if w+sln else None,
            "net_r": round(net, 2),
            "avg_rr": round(sum(x["rr"] for x in signals)/len(signals), 2) if signals else None,
        },
        "by_engine": by,
        "diagnostics": counters,
        "signals": signals,
        "notes": [
            "Event-driven replay: an FVG is created only from already-closed candles and can trigger only on a later closed candle.",
            "No future candle is scanned to decide whether an FVG will retest.",
            "Warmup data is retained; only signals inside the requested OOS window are counted.",
            "One active trade at a time inside the OOS evaluation.",
            "Fixed rules; no parameter tuning; research only.",
        ],
    }
    p = Path("backtest/combined_gold_hunter_event_driven_results.json")
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
