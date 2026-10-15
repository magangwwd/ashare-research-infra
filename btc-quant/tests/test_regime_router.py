"""Deterministic tests for the regime router. Synthetic only; no performance claims."""
import datetime as dt
import importlib.util
import json
import math
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("router_test_module", ROOT / "regime" / "router.py")
router = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = router
spec.loader.exec_module(router)


CFG = {
    "high_vol_percentile": 0.90,
    "high_vol_atr_pct": 0.05,
    "trend_score_up": 2/3,
    "trend_score_down": 1/3,
    "trend_er_min": 0.35,
    "up_ema_gap_min": 0.03,
    "down_ema_gap_max": -0.03,
    "range_er_max": 0.30,
    "range_vol_percentile_max": 0.85,
    "range_ema_gap_max": 0.20,
    "recovery_trend_score_min": 1/3,
    "trend_cap": 0.50,
    "recovery_cap": 0.25,
    "transition_cap": 0.25,
    "grid_account_cap": 0.25,
    "grid_atr_multiple": 0.90,
    "grid_min_pct": 0.018,
    "grid_max_pct": 0.035,
    "grid_max_layers": 5,
    "grid_size_multipliers": [1.0, 1.15, 1.25, 1.35, 1.50],
    "grid_basket_tp_pct": 0.008,
    "confirm_days": 2,
    "min_hold_days": 7,
    "emergency_min_hold_days": 3,
    "emergency_exit_confirm_days": 3,
}


def synthetic(n=500, mode="range"):
    start = dt.date(2024, 1, 1)
    rows = []
    for i in range(n):
        if mode == "up":
            close = 10000 * math.exp(0.0022 * i)
        elif mode == "down":
            close = 10000 * math.exp(-0.0022 * i)
        elif mode == "shock":
            close = 10000 if i < 470 else 10000 * (0.93 ** (i - 469))
        else:
            close = 10000 * math.exp(0.05 * math.sin(i / 9.0))
        prev = rows[-1]["close"] if rows else close
        high = max(close, prev) * 1.008
        low = min(close, prev) * 0.992
        rows.append({
            "date": start + dt.timedelta(days=i),
            "open": prev,
            "high": high,
            "low": low,
            "close": close,
            "volume": 10.0,
        })
    return rows


class RegimeRouterTests(TestCase):
    def test_features_are_causal(self):
        x = synthetic(500, "range")
        a = router.compute_features(x)
        x2 = [r.copy() for r in x]
        x2[-1]["close"] *= 1.50
        x2[-1]["high"] *= 1.50
        b = router.compute_features(x2)
        self.assertEqual(
            [router.asdict(r) for r in a[:-1]],
            [router.asdict(r) for r in b[:-1]],
        )

    def test_uptrend_classifies_as_trend_up(self):
        f = router.compute_features(synthetic(500, "up"))[-1]
        self.assertEqual(router.classify(f, CFG), "TREND_UP")

    def test_downtrend_classifies_as_trend_down(self):
        f = router.compute_features(synthetic(500, "down"))[-1]
        self.assertEqual(router.classify(f, CFG), "TREND_DOWN")

    def test_range_classifies_as_range(self):
        f = router.compute_features(synthetic(500, "range"))[-1]
        self.assertEqual(router.classify(f, CFG), "RANGE")

    def test_grid_spacing_is_bounded(self):
        f = router.compute_features(synthetic(500, "range"))[-1]
        rec = router.recommendation("RANGE", f, CFG)
        self.assertEqual(rec["max_layers"], 5)
        self.assertGreaterEqual(rec["grid_spacing_pct"], 0.018)
        self.assertLessEqual(rec["grid_spacing_pct"], 0.035)

    def test_state_machine_is_causal(self):
        fs = router.compute_features(synthetic(500, "range"))
        states = router.smooth_regimes(fs, CFG)
        self.assertTrue(states)
        self.assertIn("raw_regime", states[-1])
        self.assertIn("confirmed_regime", states[-1])
        self.assertIn("strategy", states[-1])

    def test_config_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cfg.json"
            p.write_text(json.dumps(CFG), encoding="utf8")
            loaded = router.load_config(p)
            self.assertEqual(loaded["grid_max_layers"], 5)


if __name__ == "__main__":
    main()
