import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LIVE = Path("data/xauusd_v8_live.json")
STATE = Path("data/xauusd_v8_telegram_state.json")

live = json.loads(LIVE.read_text(encoding="utf-8"))
signal = live.get("signal")
if live.get("status") != "SIGNAL" or not signal:
    print("No V8 signal to notify.")
    raise SystemExit(0)

key = live.get("signal_key") or f"{signal['direction']}|{signal['confirmation_time']}"
state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
if state.get("last_sent_signal_key") == key:
    print("Already notified:", key)
    raise SystemExit(0)

dt = datetime.fromisoformat(signal["confirmation_time"].replace("Z", "+00:00"))
iran = dt.astimezone(ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M")
message = (f"🥇 XAUUSD V8 Cap4 — {signal['direction']} SIGNAL\n\n"
           f"📍 Entry: {signal['entry']}\n"
           f"🛑 SL: {signal['sl']}\n"
           f"🎯 TP: {signal['tp']}\n"
           f"📊 R:R: 1:{signal['rr']}\n"
           f"⏰ {iran} به وقت ایران 🇮🇷\n\n"
           "⚠️ Signal-Only — Auto Trade DISABLED")

url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage"
body = urllib.parse.urlencode({"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": message}).encode()
req = urllib.request.Request(url, data=body, method="POST")
with urllib.request.urlopen(req, timeout=30) as response:
    result = json.load(response)
if not result.get("ok"):
    raise RuntimeError(result)

STATE.write_text(json.dumps({"last_sent_signal_key": key}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("Telegram notification sent:", key)
