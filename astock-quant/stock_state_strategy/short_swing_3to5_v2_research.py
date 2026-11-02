"""Chronological, frozen-parameter research variants of the 3–5 session PAPER strategy.

No orders, scheduler or broker; all features observed no later than signal-day close.
Uses the audited V1 execution simulator unchanged, and compares V1 to predeclared
risk filters rather than back-fitting stop/target prices against known winners.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from short_swing_3to5_v1 import Policy, build_signals, load_gold, simulate

VERSION = 'MAINBOARD_SWING_3TO5_RISK_FILTER_SHADOW_V2'

@dataclass(frozen=True)
class FilterSpec:
    name: str
    atr14_max: float
    prior5_gain_max: float
    breakout_max: float
    candle_gain_max: float

# Lock before viewing the results of this runner. Do not auto-select the winner.
SPECS = (
    FilterSpec('DEFENSIVE', 0.04, 0.08, 0.02, 0.04),
    FilterSpec('BALANCED', 0.05, 0.10, 0.03, 0.05),
    FilterSpec('LOOSE', 0.06, 0.15, 0.05, 0.07),
)


def signal_day_features(panel: pd.DataFrame) -> pd.DataFrame:
    """All fields known at completed day close; 5-day comparison uses past bars."""
    x = panel.sort_values(['vendor_symbol', 'trade_date'], kind='stable').copy()
    if x.duplicated(['vendor_symbol', 'trade_date']).any():
        raise ValueError('Duplicate symbol/date in features')
    for f in ('adjustment_factor', 'close', 'open', 'high', 'low'):
        if not pd.to_numeric(x[f], errors='coerce').gt(0).all():
            raise ValueError('Unusable OHLC/factor')
    x['adj_close'] = x.close * x.adjustment_factor
    x['adj_high'] = x.high * x.adjustment_factor
    x['adj_low'] = x.low * x.adjustment_factor
    groups = x.groupby('vendor_symbol', sort=False)
    prior_close = groups.adj_close.shift(1)
    tr = pd.concat([
        x.adj_high - x.adj_low,
        (x.adj_high - prior_close).abs(),
        (x.adj_low - prior_close).abs(),
    ], axis=1).max(axis=1)
    x['atr14_pct'] = tr.groupby(x.vendor_symbol).transform(
        lambda a: a.rolling(14, min_periods=14).mean()) / x.adj_close
    x['prior5_gain'] = x.adj_close / groups.adj_close.shift(5) - 1
    x['breakout'] = x.adj_close / groups.adj_close.transform(
        lambda a: a.shift(1).rolling(20, min_periods=20).max()) - 1
    x['candle_gain'] = x.close / x.open - 1
    return x.set_index(['trade_date', 'vendor_symbol'])[
        ['atr14_pct', 'prior5_gain', 'breakout', 'candle_gain']]


def filter_signals(baseline: dict, features: pd.DataFrame, spec: FilterSpec) -> tuple[dict, dict]:
    """Keep candidates only; preserve relative original score ranking / one-stock rule."""
    kept = {}
    reasons = Counter()
    for day, ranked in baseline.items():
        selected = []
        for symbol, score in ranked:
            key = (pd.Timestamp(day), symbol)
            if key not in features.index:
                reasons['NO_FEATURES'] += 1
                continue
            row = features.loc[key]
            if not bool(row.notna().all()):
                reasons['NO_FEATURES'] += 1
            elif row.atr14_pct > spec.atr14_max:
                reasons['ATR_OVER_CAP'] += 1
            elif row.prior5_gain > spec.prior5_gain_max:
                reasons['PRIOR_GAIN_OVER_CAP'] += 1
            elif row.breakout > spec.breakout_max:
                reasons['BREAKOUT_OVER_CAP'] += 1
            elif row.candle_gain > spec.candle_gain_max:
                reasons['CANDLE_OVER_CAP'] += 1
            else:
                selected.append((symbol, score))
        kept[day] = selected
    return kept, {'original_candidates': sum(map(len, baseline.values())),
                  'remaining_candidates': sum(map(len, kept.values())),
                  'rejected_by_reason': dict(sorted(reasons.items()))}


def period_metrics(backtest: dict) -> dict:
    """Calendar-year ledger analysis. Report trades and NAV independently."""
    out = {}
    nav = backtest['daily_nav']
    trades = backtest['trades']
    for year in sorted({p['date'][:4] for p in nav}):
        curve = [p for p in nav if p['date'].startswith(year)]
        starts = [p for p in nav if p['date'] < year+'-01-01']
        first = starts[-1]['equity_cny'] if starts else backtest['policy']['initial_cash_cny']
        completed = [t for t in trades if t['entry_date'].startswith(year)]
        prior_peak = first
        dd = 0.
        for row in curve:
            prior_peak = max(prior_peak, row['equity_cny'])
            dd = min(dd, row['equity_cny']/prior_peak-1)
        out[year] = {
            'entry_year_closed_trades': len(completed),
            'entry_year_net_pnl_cny': round(sum(t['pnl_cny'] for t in completed),2),
            'year_equity_change': round(curve[-1]['equity_cny']/first-1,6) if curve else None,
            'year_max_drawdown': round(dd,6),
            'closed_trade_win_rate': round(sum(t['pnl_cny']>0 for t in completed)/len(completed),6) if completed else None,
        }
    return out


def compact(name: str, sim_result: dict, filter_counts: dict | None) -> dict:
    return {
        'name': name, 'summary': sim_result['summary'], 'by_year': period_metrics(sim_result),
        'exit_reasons': dict(sorted(Counter(t['exit_reason'] for t in sim_result['trades']).items())),
        'holding_sessions': dict(sorted(Counter(t['holding_sessions'] for t in sim_result['trades']).items())),
        'gap_stop_worse_than_5pct': sum(t['net_return'] < -.05 and 'STOP' in t['exit_reason'] for t in sim_result['trades']),
        'trade_ledger': sim_result['trades'], 'orders': sim_result['orders'],
        'filter_statistics': filter_counts,
    }


def compare(panel: pd.DataFrame, risk, policy: Policy, start: str, end: str) -> dict:
    """Reuses exactly the V1 fills/risk model, so only pre-entry candidate removal varies."""
    all_signals = build_signals(panel, policy, risk)
    features = signal_day_features(panel)
    baseline = simulate(panel, risk, policy, signals=all_signals, start=start)
    variants = [compact('BASELINE_V1_UNCHANGED', baseline, None)]
    for spec in SPECS:
        signals, counts = filter_signals(all_signals, features, spec)
        simulation = simulate(panel, risk, policy, signals=signals, start=start)
        variants.append(compact(spec.name, simulation, counts))
    return {
        'version':VERSION, 'mode':'RESEARCH_ONLY_NO_BROKER_NO_LIVE_TRADING',
        'period':{'start':start, 'end':end}, 'policy':asdict(policy),
        'fixed_specs': [asdict(s) for s in SPECS],
        'feature_clock':'As of signal-day completed 15:00 close, enter only at next session',
        'variants':variants,
        'disclosures':[
            'All variants and the V1 baseline share identical historical execution, costs and T+1 assumptions.',
            '2024, 2025 and 2026 observations have already been reviewed; NO untouched out-of-sample profitability claim.',
            'No variant is declared a winner based on this historical sweep; selection requires forward paper testing.',
            'Fixed CNY 30k minimum causes capital-ineligibility after a drawdown; do not loosen the rule unnoticed.',
            'ATR14, prior5, breakout and candle features use known signal-date information only.',
            'Intraday exact order-book fills and 15m marketwide enhancements are not proven.',
        ],
    }


def main() -> None:
    p=argparse.ArgumentParser(description='Compare fixed predeclared V2 risk filters to untouched V1')
    p.add_argument('--market-root',type=Path,required=True)
    p.add_argument('--risk-root',type=Path,required=True)
    p.add_argument('--start',default='2024-01-01')
    p.add_argument('--end',required=True)
    p.add_argument('--budget-cny',type=float,default=40000.)
    p.add_argument('--cash-cny',type=float,default=50000.)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    now=datetime.now(ZoneInfo('Asia/Shanghai'))
    if pd.Timestamp(args.end).date()>now.date() or (pd.Timestamp(args.end).date()==now.date() and now.hour<16):
        p.error('Only past fully closed sessions are permitted')
    policy=Policy(order_budget_cny=args.budget_cny,initial_cash_cny=args.cash_cny)
    policy.validate()
    panel, manifest, sha=load_gold(args.market_root,args.start,args.end)
    from gold_pit_risk_guards import GoldPITRisk
    risk=GoldPITRisk(args.risk_root)
    if not risk.limits or not risk.names: raise RuntimeError('PIT risk evidence unavailable')
    report=compare(panel,risk,policy,args.start,args.end)
    report['gold_source']={'as_of':manifest['as_of_date'],'sha256':sha,'pit':risk.summary()}
    if args.output.exists(): raise FileExistsError('Output exists; immutable evidence required')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'status':'RESEARCH_ONLY','variants':[
        {'name':x['name'],**x['summary']} for x in report['variants']]}))

if __name__=='__main__': main()
