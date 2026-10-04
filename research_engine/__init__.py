"""Task 02 engineering-only research and backtest primitives.

This package intentionally does not assert strategy efficacy.  It reads only
validated Task 01 Clean production and keeps signal time separate from execution
time so future information is not silently used.
"""

from .backtest import BacktestConfig, BacktestResult, run_backtest
from .data import CleanResearchData
from .factors import add_engineering_factors
from .prices import add_adjusted_prices
from .signals import rank_long_only
from .universe import filter_historical_universe
from .walkforward import walk_forward_splits

__all__ = [
    "BacktestConfig", "BacktestResult", "CleanResearchData",
    "add_adjusted_prices", "filter_historical_universe",
    "add_engineering_factors", "rank_long_only", "run_backtest",
    "walk_forward_splits",
]
