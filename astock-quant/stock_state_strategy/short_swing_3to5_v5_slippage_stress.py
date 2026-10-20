"""Full capital-path spread stress on frozen V5 ranking; no new signals.

Standard paper slip .001 each fill; prespecified adverse .003 and .005
each fill (before observing these stress outcomes). Slippage changes both
BUY and SELL order prices, stop/target thresholds and available cash.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path

from short_swing_3to5_v1 import Policy, load_gold
from short_swing_3to5_v5_causal_ranking import run_rank_comparison

RATES=(0.003,0.005)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--market-root',required=True,type=Path)
    p.add_argument('--risk-root',required=True,type=Path)
    p.add_argument('--start',required=True)
    p.add_argument('--end',required=True)
    p.add_argument('--gold-manifest-sha256',required=True)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args()
    panel,manifest,actual_hash=load_gold(a.market_root,a.start,a.end)
    if actual_hash!=a.gold_manifest_sha256:
        raise ValueError('Stress source differs from frozen base comparison')
    from gold_pit_risk_guards import GoldPITRisk
    risk=GoldPITRisk(a.risk_root)
    if not risk.limits or not risk.names:raise ValueError('Missing PIT')
    runs=[]
    for slip in RATES:
        policy=replace(Policy(),slippage_rate=slip)
        policy.validate()
        report=run_rank_comparison(panel,risk,policy,a.start)
        runs.append({'slippage_each_fill':slip,
                     'results':[{'name':v['name'],'summary':v['summary'],
                                 'by_year':v['by_year'],
                                 'assumed_intraday_fills':v['assumed_intraday_fills'],
                                 'trade_dates':[(t['entry_date'],t['exit_date'],t['symbol'])
                                                for t in v['trades']]}
                                for v in report['variants']]})
    result={'version':'V5_CAUSAL_RANK_FULL_CAPITAL_SLIPPAGE_STRESS_1',
            'mode':'RESEARCH_ONLY_NO_OOS_NO_LIVE',
            'predeclared_slippage_each_fill':RATES,
            'policy_reference':asdict(Policy()),
            'gold_manifest_sha256':actual_hash,'period':{'start':a.start,'end':a.end},
            'runs':runs,
            'limitations':['Daily OHLC synthetic exits still assume executable stop/target.',
                           'Daily limits/ST/PIT knowns do not prove queues or fill availability.',
                           'All dates studied before these experiments; NOT untouched OOS.',
                           'Uniform slippage cannot replicate price-limit freezes, gaps or bid/ask depth.',
                           'No retroactive trade deletion or automatic winner selection.']}
    if a.output.exists():raise FileExistsError('No overwrite')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for x in runs:
        print('STRESS_FULL_CAPITAL_PATH',x['slippage_each_fill'],
              [(z['name'],z['summary']['total_return'],z['summary']['max_drawdown'],
                z['summary']['closed_trades']) for z in x['results']])


if __name__=='__main__':main()
