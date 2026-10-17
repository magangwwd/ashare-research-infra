"""The risk study must not change our audited VOL_STATIC baseline."""
import datetime as dt
import importlib.util
import math
import sys
from pathlib import Path
from unittest import TestCase

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"regime"/"vol_risk_study.py"
spec=importlib.util.spec_from_file_location("vol_risk_unit",SCRIPT)
study=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=study
spec.loader.exec_module(study)


def synthetic(n=1130, shock=False):
    out=[]
    start=dt.date(2023,1,1)
    for i in range(n):
        trend=math.exp(.0002*i+.018*math.sin(i/11))
        if shock and i>=1020:
            trend *= .982**min(22,i-1019)
        close=10000*trend
        before=out[-1]["close"] if out else close
        out.append({"date":start+dt.timedelta(days=i),
                    "open":before,
                    "high":max(close,before)*1.008,
                    "low":min(close,before)*.992,
                    "close":close,"volume":100.})
    return out


def fold_data(data):
    states=[]
    for x in data[180:]:
        d=x["date"].isoformat()
        states.append({"date":d,"confirmed_regime":"RANGE","raw_regime":"RANGE","strategy":"HMG"})
    return states,study.STAGE.create_folds(data,states)


class VolRiskStudyTests(TestCase):
    def test_baseline_exactly_matches_previous_engine(self):
        data=synthetic(1130,shock=True)
        states,folds=fold_data(data)
        f=study.features(data)
        scales,_=study.train_only_scales(data,folds,f)
        for multiplier in (1,2):
            new,curve,trades,_,_=study.simulate(data,folds,f,"VOL_BASE",multiplier,scales)
            old,old_curve,old_trades,_,_=study.STAGE.simulate(data,states,folds,"VOL_STATIC",multiplier)
            self.assertEqual(new["trades"],old["trades"])
            self.assertEqual(len(curve),len(old_curve))
            self.assertAlmostEqual(new["final_nav"],old["final_nav"],places=7)
            self.assertLess(max(abs(a["nav"]-b["nav"]) for a,b in zip(curve,old_curve)),1e-6)

    def test_training_scales_are_computed_before_test(self):
        data=synthetic()
        states,folds=fold_data(data)
        before,_=study.train_only_scales(data,folds,study.features(data))
        data[-1]["close"]*=.7
        after,_=study.train_only_scales(data,folds,study.features(data))
        self.assertEqual(before,after)

    def test_risk_overlay_has_no_leverage_or_same_day_fills(self):
        data=synthetic(shock=True)
        _,folds=fold_data(data)
        f=study.features(data)
        scales,_=study.train_only_scales(data,folds,f)
        for v in study.VARIANTS:
            result,curve,trades,events,fold_stats=study.simulate(data,folds,f,v,1,scales)
            self.assertTrue(all(t["signal_asof"] < t["fill_date"] for t in trades))
            self.assertTrue(all(c["cash_usdt"]>=-1e-7 and c["btc_qty"]>=-1e-10 for c in curve))
            self.assertTrue(all(c["nav"]>0 for c in curve))
            self.assertTrue(all(fold_stats[i]["start_nav"]==fold_stats[i-1]["end_nav"]
                                for i in range(1,len(fold_stats))))
            self.assertLessEqual(result["max_btc_weight"],.51)
            self.assertLessEqual(result["trades"],len(curve))

    def test_dd_cap_never_increases_risk_between_rebalances(self):
        data=synthetic(shock=True)
        _,folds=fold_data(data)
        f=study.features(data)
        scale,_=study.train_only_scales(data,folds,f)
        _,_,trades,events,_=study.simulate(data,folds,f,"VOL_DD5",1,scale)
        for t in trades:
            if t["reason"]=="SOFT_DD_25PCT_CAP":
                self.assertEqual(t["side"],"SELL")
                self.assertTrue(t["emergency"])
            if t["reason"]=="WEEKLY_VOL_REBALANCE":
                d=dt.date.fromisoformat(t["fill_date"])
                self.assertEqual(d.weekday(),0)

    def test_no_lookahead_past_account_or_signal_when_last_bar_changes(self):
        data=synthetic(1130)
        _,folds=fold_data(data)
        f=study.features(data)
        scales,_=study.train_only_scales(data,folds,f)
        _,curve,trades,_,_=study.simulate(data,folds,f,"VOL_SHOCK20",1,scales)
        altered=[dict(x) for x in data]
        altered[-1]["close"]*=.81
        altered[-1]["low"]=min(altered[-1]["low"],altered[-1]["close"]*.999)
        f2=study.features(altered)
        scales2,_=study.train_only_scales(altered,folds,f2)
        _,curve2,trades2,_,_=study.simulate(altered,folds,f2,"VOL_SHOCK20",1,scales2)
        self.assertEqual(curve[:-1],curve2[:-1])
        self.assertEqual(trades,trades2)
