"""Synthetic adversarial feature-clock/contract tests; not historical PnL proof."""
import unittest
from unittest.mock import patch

import pandas as pd

from short_swing_3to5_v1 import Policy
from short_swing_3to5_v2_research import (
    SPECS, compare, filter_signals, signal_day_features, period_metrics
)


class Allow:
    def entry_reason(self, *_): return None
    def exit_reason(self, *_): return None


def make_panel():
    days=pd.bdate_range('2024-01-01',periods=85)
    rows=[]
    for name in ('600001.SH','600002.SH'):
        for i,d in enumerate(days):
            close=10.
            if name=='600001.SH' and i>=63: close=12.0
            rows.append({'vendor_symbol':name,'trade_date':d,
                         'open':close*0.995,'high':close*1.02,
                         'low':close*.98,'close':close,
                         'adjustment_factor':1.,'volume_shares':1000000.,
                         'turnover_cny':close*1e6,
                         'is_quality_valid':True})
    return pd.DataFrame(rows), days


class RiskFilter(unittest.TestCase):
    def test_features_lag_order_and_risk_limits(self):
        panel,days=make_panel()
        features=signal_day_features(panel)
        idx=(days[64],'600001.SH')
        self.assertGreater(features.loc[idx,'prior5_gain'],.1)
        self.assertLess(features.loc[(days[64],'600002.SH'),'prior5_gain'],.01)
        baseline={days[64]:[('600001.SH',.2),('600002.SH',.1)]}
        cleaned,stats=filter_signals(baseline,features,SPECS[1])
        self.assertEqual(cleaned[days[64]],[('600002.SH',.1)])
        self.assertEqual(stats['remaining_candidates'],1)
        self.assertEqual(stats['original_candidates'],2)
        # Future bar modifications must not change signal-day features.
        alt=panel.copy()
        alt.loc[alt.trade_date.gt(days[64]),'close']=999.
        new=signal_day_features(alt)
        pd.testing.assert_series_equal(features.loc[idx],new.loc[idx])

    def test_missing_feature_fails_closed(self):
        panel,days=make_panel()
        features=signal_day_features(panel)
        baseline={days[0]:[('600001.SH',1.)],days[64]:[('600004.SH',.8)]}
        cleaned,stats=filter_signals(baseline,features,SPECS[1])
        self.assertEqual(stats['remaining_candidates'],0)
        self.assertEqual(stats['rejected_by_reason']['NO_FEATURES'],2)

    def test_baseline_unchanged_and_entry_substitution(self):
        panel,days=make_panel()
        fixed={days[64]:[('600001.SH',.2),('600002.SH',.1)]}
        with patch('short_swing_3to5_v2_research.build_signals',return_value=fixed):
            result=compare(panel,Allow(),Policy(),str(days[0].date()),str(days[-1].date()))
        names=[v['name'] for v in result['variants']]
        self.assertEqual(names,['BASELINE_V1_UNCHANGED','DEFENSIVE','BALANCED','LOOSE'])
        baseline=result['variants'][0]
        self.assertEqual(baseline['orders'][0]['symbol'],'600001.SH')
        self.assertEqual(result['variants'][2]['orders'][0]['symbol'],'600002.SH')
        for variant in result['variants']:
            self.assertTrue(all(o['shares']%100==0 for o in variant['orders']))
            self.assertLessEqual(max((o.get('notional_cny',0) for o in variant['orders']),default=0),50000)
        self.assertEqual(baseline['summary']['closed_trades'],1)
        self.assertIn('2024',period_metrics({'daily_nav':[{'date':'2024-01-02','equity_cny':50000.}],
                   'trades':[], 'policy':{'initial_cash_cny':50000}}))

if __name__=='__main__':unittest.main()
