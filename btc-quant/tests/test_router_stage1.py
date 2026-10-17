"""Real production configuration and stage-1 model invariant tests."""
import csv
import datetime as dt
import importlib.util
import json
import math
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

BASE = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


router = load("audit_router_module", BASE/"regime"/"router.py")
stage = load("audit_stage_module", BASE/"regime"/"r1_experiment.py")


def rows(n=1150):
    base = dt.date(2023, 1, 1)
    out = []
    for i in range(n):
        c = 10000*math.exp(.03*math.sin(i/9))
        if i >= 1090:
            c *= .99**(i-1089)
        old = out[-1]["close"] if out else c
        out.append({"date":base+dt.timedelta(days=i),"open":old,
                    "high":max(c,old)*1.01,"low":min(c,old)*.99,
                    "close":c,"volume":100 + (500 if i >= 1090 else 0)})
    return out


def artificial_states(data):
    state = []
    for i in range(180,len(data)):
        g = "RANGE" if i%170<140 else "TRANSITION"
        state.append({"date":data[i]["date"].isoformat(),
                      "confirmed_regime":g,"raw_regime":g,
                      "strategy":"HMG" if g=="RANGE" else "CASH"})
    return state


class StageOneTests(TestCase):
    def test_nested_production_config_is_usable_and_transition_cash(self):
        cfg = router.load_config(BASE/"config"/"regime_router_20261016.json")
        self.assertEqual(cfg["transition_cap"], 0)
        f = router.compute_features(rows())[-1]
        self.assertEqual(router.recommendation("TRANSITION",f,cfg)["target_cap"],0)

    def test_trend_vote_thresholds_are_exact(self):
        cfg = router.load_config(BASE/"config"/"regime_router_20261016.json")
        f = router.compute_features(rows())[-1]
        from dataclasses import replace
        f = replace(f, trend_score=2/3, er60=0.8, ema200_gap=.1,
                    ema200_slope30=.02, vol_percentile=.1, atr14_pct=.01)
        self.assertEqual(router.classify(f,cfg),"TREND_UP")
        f = replace(f, trend_score=1/3, ema200_gap=-.1, ema200_slope30=-.02)
        self.assertEqual(router.classify(f,cfg),"TREND_DOWN")

    def test_train_mapping_uses_no_test_data(self):
        data = rows()
        states = artificial_states(data)
        a = stage.create_folds(data,states)
        modified = [r.copy() for r in data]
        modified[-1]["close"] *= .8
        b = stage.create_folds(modified,states)
        self.assertEqual(a,b)
        self.assertEqual(a[0]["train_end"],data[909]["date"].isoformat())
        self.assertEqual(a[0]["test_start"],data[910]["date"].isoformat())

    def test_continuous_account_and_next_open_and_price_guard(self):
        data = rows()
        states = artificial_states(data)
        folds = stage.create_folds(data,states)
        result,curve,trades,events,folds_report = stage.simulate(data,states,folds,"R1_PRICE",1)
        self.assertEqual(len(curve),len(data)-910)
        self.assertLessEqual(result["max_grid_layers"],5)
        self.assertTrue(all(t["fill_date"]>t["signal_asof"] for t in trades))
        self.assertTrue(all(x["nav"]>0 and x["cash_usdt"]>=-1e-6 for x in curve))
        self.assertTrue(all(abs(t["cost_usdt"]-.0015*t["btc_qty"]*t["open_price"])<1e-8 for t in trades))
        self.assertTrue(all(folds_report[i]["start_nav"]==folds_report[i-1]["end_nav"]
                            for i in range(1,len(folds_report))))
        self.assertTrue(any(e["event"]=="WATCH" for e in events))

    def test_two_cost_scenarios_not_reusing_cash(self):
        data = rows()
        states = artificial_states(data)
        folds = stage.create_folds(data,states)
        a,*_ = stage.simulate(data,states,folds,"R0",1)
        b,*_ = stage.simulate(data,states,folds,"R0",2)
        self.assertTrue(math.isfinite(a["final_nav"]))
        self.assertTrue(math.isfinite(b["final_nav"]))
        self.assertLessEqual(a["max_grid_layers"],5)
        self.assertLessEqual(b["max_grid_layers"],5)

    def test_static_benchmarks_use_same_folds_and_shared_cash(self):
        data = rows()
        states = artificial_states(data)
        folds = stage.create_folds(data,states)
        cash,eq,t,ev,f = stage.simulate(data,states,folds,"CASH_STATIC",1)
        self.assertEqual(cash["final_nav"],1000.0)
        self.assertEqual(cash["trades"],0)
        self.assertEqual(cash["max_drawdown"],0.0)
        for variant in ("V3_STATIC","VOL_STATIC"):
            result,curve,trades,_,_ = stage.simulate(data,states,folds,variant,1)
            self.assertTrue(math.isfinite(result["final_nav"]))
            self.assertEqual(len(curve),len(eq))
            self.assertTrue(all(t["fill_date"]>t["signal_asof"] for t in trades))
            self.assertEqual(result["max_grid_layers"],0)
