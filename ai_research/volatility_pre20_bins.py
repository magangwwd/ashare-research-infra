"""Fixed 20-day prior return bins after already-frozen volatility-expansion signal.

Reads archived FIVE_STOCK_PRE_MOVE_KLINE_V1 statistics.json. Reports return
on fifth session, NOT five-day adverse price excursion (requires Gold lows).
No tuning, no order placement; events overlap unless separately thinned.
"""
import argparse
import json
from pathlib import Path
import pandas as pd

LABELS = ("below_0", "0_to_5", "5_to_10", "10_to_20", "above_20")
def group_for(x):
    return LABELS[0] if x < 0 else LABELS[1] if x < .05 else (
        LABELS[2] if x < .10 else LABELS[3] if x < .20 else LABELS[4])

def event_rows(document):
    if (document.get("version")!="FIVE_STOCK_PRE_MOVE_KLINE_V1" or
        document.get("as_of")!="2026-10-16" or
        document.get("authorization")!="RESEARCH_ONLY_NO_TRADING"):
        raise ValueError("Reject unverified source packet")
    events=[]
    for r in document["rows"]:
        if r["horizon"]!=5 or r["features"]["frozen_vol_expansion"]!=1:
            continue
        pre=float(r["features"]["prior20_return"])
        events.append({
            "stock":r["symbol"],"signal_date":r["day"],
            "period":r["period"],"session":r["index"],
            "prior20":pre,"bin":group_for(pre),
            "future5_close_to_close":r["future_return"]})
    if not events:
        raise ValueError("Frozen expansion events missing")
    return pd.DataFrame(events)

def isolate_nonoverlap(df):
    kept=[]
    for _,group in df.sort_values("session").groupby("stock"):
        last=-100000
        for _,row in group.iterrows():
            if row["session"]-last>5:
                kept.append(row);last=row["session"]
    return pd.DataFrame(kept)

def summarize(frame):
    out=[]
    for period in ("all","2023_2024","2025","2026"):
        data=frame if period=="all" else frame.loc[frame.period.eq(period)]
        for label in LABELS:
            x=data.loc[data.bin.eq(label),"future5_close_to_close"]
            out.append({"period":period,"prior20_bin":label,"events":len(x),
                "mean5":float(x.mean()) if len(x) else None,
                "median5":float(x.median()) if len(x) else None,
                "up_rate":float((x>0).mean()) if len(x) else None,
                "down5_rate":float((x<=-.05).mean()) if len(x) else None,
                "worst5":float(x.min()) if len(x) else None})
    return pd.DataFrame(out)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",type=Path,required=True)
    p.add_argument("--output-dir",type=Path,required=True)
    args=p.parse_args()
    rows=event_rows(json.loads(args.input.read_text(encoding="utf-8")))
    args.output_dir.mkdir(parents=True,exist_ok=True)
    rows.to_csv(args.output_dir/"events.csv",index=False)
    summarize(rows).to_csv(args.output_dir/"grouped.csv",index=False)
    thinned=isolate_nonoverlap(rows)
    summarize(thinned).to_csv(args.output_dir/"grouped_nonoverlap.csv",index=False)
    (args.output_dir/"README.md").write_text(
        "# 波动扩大 × 前20日涨幅分档\n\n"
        "沿用FIVE_STOCK_PRE_MOVE_KLINE_V1的五股原始信号。"
        "仅统计信号后第5交易日的收盘相对变化，绝不冒充最大回撤。\n\n"
        "2025/2026已经被观察过，不构成未见样本。"
        "原始样本高度相关，非重叠统计同样无法消除跨股票共振。"
        "五日内最低价、次日可成交价、交易费用、涨跌停限制等"
        "必须通过Gold日线/分钟数据另行验证后才能进入交易回测。\n",
        encoding="utf-8")
    print(json.dumps({"status":"RESEARCH_ONLY","events":len(rows),
                      "nonoverlap":len(thinned)}))

if __name__=="__main__":
    main()
