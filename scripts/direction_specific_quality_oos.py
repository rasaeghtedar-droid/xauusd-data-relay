#!/usr/bin/env python3
import json
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
src=json.loads((ROOT/"backtest/liquidity_hunter_v3_quality_score.json").read_text(encoding="utf-8"))
# Analyze only the previously untouched OOS period.
tr=[x for x in src["details"] if x["candle_time"][:7] in ("2026-08","2026-09")]

def s(xs):
 tp=sum(x["outcome"]=="TP" for x in xs); sl=sum(x["outcome"]=="SL" for x in xs)
 net=sum(x["rr"] if x["outcome"]=="TP" else -1 if x["outcome"]=="SL" else 0 for x in xs)
 return {"trades":len(xs),"tp":tp,"sl":sl,"win_rate":round(tp/(tp+sl)*100,2) if tp+sl else 0,
         "net_R":round(net,2),"avg_RR":round(sum(x["rr"] for x in xs)/len(xs),2) if xs else 0}

def group(xs,key):
 d=defaultdict(list)
 for x in xs:d[key(x)].append(x)
 return {str(k):s(v) for k,v in sorted(d.items(),key=lambda z:str(z[0]))}

# Direction-specific diagnostics. These are diagnostics, not new production rules.
def buy_quality(x):
 score=0
 if x["confirmation"]=="bullish continuation": score+=2
 elif x["confirmation"]=="bullish structure break": score+=1
 elif x["confirmation"]=="bullish displacement": score+=1
 if x["h1_bias"]=="BULLISH": score+=1
 if x["h1_bias"]=="NEUTRAL": score+=0
 return score

def sell_quality(x):
 score=0
 if x["confirmation"]=="bearish continuation": score+=2
 elif x["confirmation"]=="bearish structure break": score+=1
 elif x["confirmation"]=="bearish displacement": score+=1
 if x["h1_bias"]=="BEARISH": score+=1
 if x["h1_bias"]=="NEUTRAL": score+=0
 return score

for x in tr:
 x["direction_quality"]=buy_quality(x) if x["signal"]=="BUY" else sell_quality(x)

result={
 "period":"2026-08 through 2026-09",
 "overall":s(tr),
 "BUY":{"overall":s([x for x in tr if x["signal"]=="BUY"]),
        "quality":group([x for x in tr if x["signal"]=="BUY"],lambda x:x["direction_quality"]),
        "confirmation":group([x for x in tr if x["signal"]=="BUY"],lambda x:x["confirmation"]),
        "h1_bias":group([x for x in tr if x["signal"]=="BUY"],lambda x:x["h1_bias"])},
 "SELL":{"overall":s([x for x in tr if x["signal"]=="SELL"]),
         "quality":group([x for x in tr if x["signal"]=="SELL"],lambda x:x["direction_quality"]),
         "confirmation":group([x for x in tr if x["signal"]=="SELL"],lambda x:x["confirmation"]),
         "h1_bias":group([x for x in tr if x["signal"]=="SELL"],lambda x:x["h1_bias"])}
}
(ROOT/"backtest/direction_specific_quality_oos.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(result,ensure_ascii=False,indent=2))
