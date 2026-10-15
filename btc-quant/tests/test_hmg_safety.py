"""Deterministic HMG simulator safety tests."""
import datetime as dt
import importlib.util
import math
from pathlib import Path
from unittest import TestCase, main


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hmg", ROOT / "regime" / "hmg.py")
hmg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hmg)


def rows(n=500, mode="range"):
    start = dt.date(2024, 1, 1)
    out = []
    for i in range(n):
        if mode == "range":
            close = 10000 * math.exp(0.04 * math.sin(i / 7.0))
        elif mode == "crash":
            close = 10000 if i < 450 else 10000 * (0.92 ** (i - 449))
        else:
            close = 10000 * math.exp(0.001 * i)
        prev = out[-1]["close"] if out else close
        out.append({
            "date": start + dt.timedelta(days=i),
            "open": prev,
            "high": max(prev, close) * 1.01,
            "low": min(prev, close) * 0.99,
            "close": close,
            "volume": 100.0,
        })
    return out


class HMGSafetyTests(TestCase):
    def test_max_layers_never_exceed_five(self):
        data = rows(mode="range")
        regimes = {r["date"].isoformat(): "RANGE" for r in data}
        result = hmg.simulate(data, regimes)
        self.assertLessEqual(result["max_layer"], 5)

    def test_crash_is_not_allowed_to_create_sixth_layer(self):
        data = rows(mode="crash")
        regimes = {}
        for i, r in enumerate(data):
            regimes[r["date"].isoformat()] = "RANGE" if i < 450 else "TREND_DOWN"
        result = hmg.simulate(data, regimes)
        self.assertLessEqual(result["max_layer"], 5)

    def test_two_cost_scenario_runs(self):
        data = rows(mode="range")
        regimes = {r["date"].isoformat(): "RANGE" for r in data}
        one = hmg.simulate(data, regimes, cost_rate=0.0015)
        two = hmg.simulate(data, regimes, cost_rate=0.0030)
        self.assertTrue(math.isfinite(one["final_nav"]))
        self.assertTrue(math.isfinite(two["final_nav"]))


if __name__ == "__main__":
    main()
