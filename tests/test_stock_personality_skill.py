"""Read-only CI smoke test for stock-personality-analysis v0.1."""
from pathlib import Path
import importlib.util
import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / "skills/stock-personality-analysis/stock_personality.py"
spec = importlib.util.spec_from_file_location("stock_personality_skill", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def test_episode_spacing():
    assert module.episode_indices([1, 2, 3, 7, 10, 11, 20]) == [1, 7, 20]

def test_future_not_in_signal():
    dates = pd.bdate_range("2025-11-01", periods=60)
    df = pd.DataFrame({"ts_code":"600031.SH","trade_date":dates,"open":10.0,
                       "high":10.5,"low":9.5,"close":10.0,"vol":100.0})
    before = module.features(df)
    modified = df.copy()
    modified.loc[40,"close"] = 10.4
    after = module.features(modified)
    for name in ("A_volume_down","B_breakdown","D_breakout","F_tight_lowvol"):
        assert bool(before.loc[39,name]) == bool(after.loc[39,name])

def test_year_boundary_exclusion():
    dates = pd.bdate_range("2025-12-01", periods=50)
    df = pd.DataFrame({"ts_code":"600031.SH","trade_date":dates,"open":10.0,
                       "high":10.5,"low":9.5,"close":10.0,"vol":100.0})
    result = module.analyze(df)
    assert result["stocks"]["600031.SH"]["periods"]["2025"]["baseline"]["n"] < 30
