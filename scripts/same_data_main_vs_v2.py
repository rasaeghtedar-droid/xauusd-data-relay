#!/usr/bin/env python3
import json, importlib.util, csv, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
URL="https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv"
OUT=ROOT/"backtest"/"same_data_main_vs_v2.json"
def dt(s): return datetime.fromisoformat(s.replace("Z","+00:00"))
def load():
 r=urllib.request.urlopen(urllib.request.Request(URL,headers={"User-Agent":"xauusd-comparison/1.0"}),timeout=120).read().decode()
 out=[]
 for x in csv.DictReader(r.splitlines()):
  t=x["datetime"]; t=t if t.endswith("Z") or "+" in t else t+"+00:00"
  out.append({"openTime":dt(t).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),"open":float(x["open"]),"high":float(x["high"]),"low":float(x["low"]),"close":float(x["close"]),"volume":float(x.get("volume") or 0),"tickVolume":float(x.get("volume") or 0),"isOpen":False})
 return out
def agg(m5,n):
 out=[]; key=None; sec=n*60
 for b in m5:
  t=dt(b["openTime"]); start=datetime.fromtimestamp(int(t.timestamp())-(int(t.timestamp())%sec),tz=timezone.utc); k=start.isoformat()
  if k!=key:
   out.append({"openTime":start.strftime("%Y-%m-%dT%H:%M:%SZ"),"open":b["open"],"high":b["high"],"low":b["low"],"close":b["close"],"volume":0,"tickVolume":0,"isOpen":False}); key=k
  else:
   out[-1]["high"]=max(out[-1]["high"],b["high"]); out[-1]["low"]=min(out[-1]["low"],b["low"]); out[-1]["close"]=b["close"]
  out[-1]["volume"]+=b["volume"]; out[-1]["tickVolume"]+=b["tickVolume"]
 return out
def outcome(sig,fut):
 for b in fut:
  sl=b["low"]<=sig["sl"] if sig["signal"]=="BUY" else b["high"]>=sig["sl"]
  tp=b["high"]>=sig["tp"] if sig["signal"]=="BUY" else b["low"]<=sig["tp"]
  if sl and tp:return "AMBIGUOUS"
  if tp:return "TP"
  if sl:return "SL"
 return "OPEN"
def summary(trades):
 tp=sum(x["outcome"]=="TP" for x in trades); sl=sum(x["outcome"]=="SL" for x in trades)
 net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in trades)
 return {"trades":len(trades),"tp":tp,"sl":sl,"win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,"net_R":round(net,2),"avg_RR":round(sum(x["rr"] for x in trades)/len(trades),2) if trades else 0}
def run(mod,m5,m15,h1,v2=False):
 mod.MAX_TARGET_RR=2.5
 trades=[]; active_end=None
 for i in range(50,len(m5)):
  t=dt(m5[i]["openTime"])
  if active_end and t<=active_end: continue
  c15=[x for x in m15 if dt(x["openTime"])<=t-timedelta(minutes=15)][-120:]
  c1=[x for x in h1 if dt(x["openTime"])<=t-timedelta(hours=1)][-60:]
  if len(c15)<10 or len(c1)<4: continue
  data={"intervals":{"5m":{"bars":m5[max(0,i-499):i+1]},"15m":{"bars":c15},"1h":{"bars":c1}}}
  sig=mod.analyze(data)
  if sig.get("status")!="SETUP FOUND": continue
  if v2 and sig.get("signal")=="SELL" and sig.get("confirmation")!="short-term bearish structure break": continue
  sig={**sig,"outcome":outcome(sig,m5[i+1:])}; trades.append(sig)
  for b in m5[i+1:]:
   hit=(b["high"]>=sig["tp"] or b["low"]<=sig["sl"])
   if hit: active_end=dt(b["openTime"]); break
 return summary(trades),trades
def main():
 m5=load(); m15=agg(m5,15); h1=agg(m5,60)
 p=ROOT/"liquidity_hunter"/"liquidity_hunter.py"; spec=importlib.util.spec_from_file_location("lh",p); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
 a,ad=run(mod,m5,m15,h1,False); b,bd=run(mod,m5,m15,h1,True)
 result={"data":{"m5":len(m5),"m15":len(m15),"h1":len(h1),"first_m5":m5[0]["openTime"],"last_m5":m5[-1]["openTime"]},"main_current_cap2_5":a,"v2_sell_bearish_structure_cap2_5":b}
 OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
