"""Five-stock retrospective daily-bar breakout / pullback study, PAPER ONLY.

Five names were selected ex-post on 2026-10-09. This analysis is NOT the
frozen V1 strategy, NOT an out-of-sample result and NOT trade authorization.
"""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd

NAMES={"600351.SH":"亚宝药业","002107.SZ":"沃华医药",
       "600641.SH":"先导基电","002568.SZ":"百润股份",
       "002296.SZ":"辉煌科技"}
START=pd.Timestamp("2024-01-01")
END=pd.Timestamp("2026-10-09")
BUY_COST=0.0013
SELL_COST=0.0018
SLIP=0.001
MAX_HOLD=20


def load(root):
    specs={"daily_prices":["vendor_symbol","trade_date","open","high","low","close","volume_shares"],
           "adjustment_factors":["vendor_symbol","trade_date","adjustment_factor"]}
    combined={}
    for kind,cols in specs.items():
        pieces=[]
        for year in range(2023,2027):
            for f in sorted((root/kind/f"year={year}").glob("month=*/data.parquet")):
                df=pd.read_parquet(f,columns=cols)
                df=df[df["vendor_symbol"].isin(NAMES)]
                if len(df): pieces.append(df)
        if not pieces: raise ValueError("Missing historical partitions: "+kind)
        combined[kind]=pd.concat(pieces,ignore_index=True)
    x=combined["daily_prices"].merge(combined["adjustment_factors"],
         on=["vendor_symbol","trade_date"],how="left",validate="one_to_one")
    if x["adjustment_factor"].isna().any(): raise ValueError("Missing adjustment factors")
    if x.duplicated(["vendor_symbol","trade_date"]).any(): raise ValueError("Duplicate dates")
    if (x["adjustment_factor"]<=0).any(): raise ValueError("Bad price factors")
    x["trade_date"]=pd.to_datetime(x["trade_date"])
    if x["trade_date"].max()!=END: raise ValueError("Wrong data endpoint; refuse to test")
    for k in ("open","high","low","close"): x["p_"+k]=x[k]*x["adjustment_factor"]
    return x.sort_values(["vendor_symbol","trade_date"])


def add_features(x):
    x=x.reset_index(drop=True).copy()
    c,h,l=x["p_close"],x["p_high"],x["p_low"]
    prev=c.shift(1)
    tr=pd.concat([h-l,(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1)
    x["atr"]=tr.rolling(14).mean()
    x["ma20"]=c.rolling(20).mean()
    prevhigh=h.shift(1).rolling(20).max()
    prevvol=x["volume_shares"].shift(1).rolling(20).mean()
    v=x["volume_shares"]/prevvol
    trend=(c>x["ma20"])&(x["ma20"]>x["ma20"].shift(5))
    breakout=trend&(c>prevhigh)&(c<=1.04*prevhigh)&(v>=1.2)
    pullback=(trend&(l<=1.015*x["ma20"])&(c<=1.04*x["ma20"])
              &(c>x["p_open"])&v.between(0.5,1))
    x["pattern"]=np.select([breakout,pullback],["BREAKOUT","PULLBACK"],default="")
    return x


def exit_at(x,start,entry,risk):
    stop=entry-risk; target=entry+2*risk
    last=start+MAX_HOLD-1
    for j in range(start,last+1):
        r=x.iloc[j]; op=float(r["p_open"]); lo=float(r["p_low"]); hi=float(r["p_high"])
        if op<=stop: return j,op,"STOP_GAP"
        if op>=target: return j,op,"TARGET_GAP"
        if lo<=stop: return j,stop,"STOP"
        if hi>=target: return j,target,"TARGET"
    return last,float(x.iloc[last]["p_close"]),"TIME"


def simulate(x):
    x=add_features(x)
    trades=[]; raw=int(x["pattern"].ne("").sum()); gaps=0; risks=0; pending=0
    i=0
    while i<len(x):
        row=x.iloc[i]
        if row["trade_date"]<START or not row["pattern"]: i+=1;continue
        if i+MAX_HOLD>=len(x): pending+=1;i+=1;continue
        tomorrow=x.iloc[i+1]
        gap=float(tomorrow["p_open"]/row["p_close"]-1)
        locked=(abs(float(tomorrow["high"]-tomorrow["low"]))<1e-8
                and float(tomorrow["open"]/row["close"]-1)>0.095)
        if abs(gap)>0.03 or locked or tomorrow["volume_shares"]<=0:
            gaps+=1;i+=1;continue
        entry=float(tomorrow["p_open"])*(1+SLIP)
        risk=2*float(row["atr"])
        if not np.isfinite(risk) or not (0.015<=risk/entry<=0.10):
            risks+=1;i+=1;continue
        j,price,reason=exit_at(x,i+1,entry,risk)
        net=(price*(1-SLIP)*(1-SELL_COST))/(entry*(1+BUY_COST))-1
        trades.append({"symbol":row["vendor_symbol"],"name":NAMES[row["vendor_symbol"]],
            "pattern":row["pattern"],"signal_date":str(row["trade_date"].date()),
            "entry_date":str(tomorrow["trade_date"].date()),
            "exit_date":str(x.iloc[j]["trade_date"].date()),
            "entry_raw_approx":round(entry/float(tomorrow["adjustment_factor"]),4),
            "risk_pct":round(risk/entry,5),"hold_bars":j-i,
            "net_return":net,"net_R":net/(risk/entry),"reason":reason})
        i=j+1
    return trades,{"raw_patterns":raw,"skipped_gap":gaps,
                   "skipped_risk":risks,"incomplete_at_end":pending}


def stats(trades):
    if not trades:
        return {"trades":0,"win_rate":None,"avg_net_return":None,
                "profit_factor":None,"avg_win_loss_ratio":None,"max_sequential_drawdown":None}
    r=np.array([t["net_return"] for t in trades])
    wins=r[r>0];losses=r[r<0]
    equity=pd.Series(np.cumprod(np.r_[1,1+r]))
    dd=float((equity/equity.cummax()-1).min())
    return {"trades":len(trades),"win_rate":round(float((r>0).mean()),4),
        "avg_net_return":round(float(r.mean()),5),
        "median_net_return":round(float(np.median(r)),5),
        "profit_factor":round(float(wins.sum()/-losses.sum()),4) if len(losses) else None,
        "avg_win_loss_ratio":round(float(wins.mean()/-losses.mean()),4) if len(wins) and len(losses) else None,
        "max_sequential_drawdown":round(dd,5),
        "total_sequential_compound_return":round(float(equity.iloc[-1]-1),5),
        "worst_trade":round(float(r.min()),5),"avg_net_R":round(float(np.mean([t["net_R"] for t in trades])),4)}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    data=load(a.root); trades=[]; diag={}
    for symbol in NAMES:
        part=data[data["vendor_symbol"]==symbol]
        if len(part)<70: raise ValueError("Not enough history: "+symbol)
        result,details=simulate(part)
        diag[symbol]=details;trades.extend(result)
        print(symbol,"trades=",len(result),"patterns=",details["raw_patterns"],flush=True)
    if not trades: raise ValueError("No eligible trades: cannot calculate meaningful statistics")
    trades.sort(key=lambda t:(t["entry_date"],t["symbol"]))
    a.output.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(trades).to_csv(a.output/"trades.csv",index=False,encoding="utf-8-sig")
    groups={}
    for label in NAMES:
        groups[label]=stats([x for x in trades if x["symbol"]==label])
    for pat in ("BREAKOUT","PULLBACK"):
        groups[pat]=stats([x for x in trades if x["pattern"]==pat])
    for name,begin,end in [
        ("2024_development","2024-01-01","2024-12-31"),
        ("2025_2026_historical_diagnostic","2025-01-01","2026-10-09")]:
        groups[name]=stats([x for x in trades if begin<=x["signal_date"]<=end])
    summary={
        "status":"RETROSPECTIVE_NOT_OUT_OF_SAMPLE",
        "data_end":"2026-10-09","begin":"2024-01-01",
        "universe":NAMES,
        "severe_selection_bias":"Five symbols were selected from the future 2026-10-09 A-pool, not from historical eligible lists.",
        "limitations":["NOT the frozen LOCK_MARKUP V1 strategy.",
          "Historical shareholder/news exclusion not tested.",
          "No 15-minute or tick-based fill simulation.",
          "Corporate-action factor-adjusted prices approximate total returns; actual tax/dividends may differ.",
          "All performance is historical diagnostic. No honest unseen holdout for these five chosen names."],
        "execution":{"signal":"Close day T, simulate T+1 OPEN",
                     "breakout":"MA20 rising 5d; close above previous 20d high; volume >=1.2x previous 20d average",
                     "pullback":"MA20 rising 5d; low touches MA20+1.5%; bullish close above MA20; 0.5x-1x previous 20d average volume",
                     "entry_gap_limit":"3%","stop":"2 ATR14","target":"4 ATR14 = gross risk/reward 2:1",
                     "max_hold_days":MAX_HOLD,"simultaneous_stop_target":"stop first",
                     "buy_fee":BUY_COST,"sell_fee":SELL_COST,"slippage_per_side":SLIP},
        "overall":stats(trades),"groups":groups,"diagnostics":diag}
    (a.output/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# 五只A股入场条件：历史诊断（非前向验证）","",
           "2024-01-01至2026-10-09；2023年用于指标预热。五只股票在2026-10-09才被选出，过往收益存在显著事后选股偏差。",
           "买入信号于收盘确认，次一交易日开盘模拟入场；2ATR止损/4ATR目标，20日退出。",
           "买费0.13%、卖费0.18%，买卖各0.1%模拟滑点；同根K线先止损。退出价格使用复权近似。",
           "最大回撤为逐笔按时间顺序复投曲线的回撤，**不是五股票真实组合回撤**。","",
           "| 对象 | 交易数 | 胜率 | 单笔平均净收益 | 利润因子 | 序列最大回撤 |",
           "|---|---:|---:|---:|---:|---:|"]
    for name,item in [("全部",summary["overall"])]+list(groups.items()):
        def show(k):
            v=item.get(k)
            return "-" if v is None else str(v)
        lines.append("| "+name+" | "+show("trades")+" | "+show("win_rate")+" | "+
                     show("avg_net_return")+" | "+show("profit_factor")+" | "+
                     show("max_sequential_drawdown")+" |")
    lines+=["","## 局限"]+["- "+s for s in summary["limitations"]]
    lines+=["","原A级状态池和正式冻结V1策略均未修改；历史成绩不能作为真实买入授权。"]
    (a.output/"report.md").write_text("\n".join(lines),encoding="utf-8")
    print("BACKTEST_SUMMARY="+json.dumps(summary["overall"],ensure_ascii=False),flush=True)


if __name__=="__main__": main()
