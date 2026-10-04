"""Engineering-only end-to-end backtest smoke run on accepted Task 01 data."""
from __future__ import annotations

import argparse
import json

from .backtest import BacktestConfig, run_backtest
from .data import CleanResearchData
from .factors import add_engineering_factors
from .metrics import summarize
from .prices import add_adjusted_prices
from .readiness import readiness_report
from .signals import rank_long_only
from .universe import filter_historical_universe


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 02 engineering backtest smoke run")
    parser.add_argument("--root", default=".")
    parser.add_argument("--factor", default="momentum_20d")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--commission-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--sell-tax-bps", type=float, default=0.0)
    args = parser.parse_args()

    data = CleanResearchData(args.root)
    readiness = readiness_report(data)
    market = add_adjusted_prices(data.market())
    market = filter_historical_universe(market, data.master())
    market = add_engineering_factors(market)

    if args.factor not in market.columns:
        raise ValueError(f"unknown factor: {args.factor}")
    targets = rank_long_only(market, args.factor, top_n=args.top_n)
    result = run_backtest(
        market,
        targets,
        BacktestConfig(
            commission_bps=args.commission_bps,
            slippage_bps=args.slippage_bps,
            sell_tax_bps=args.sell_tax_bps,
        ),
    )
    output = {
        "engineering_only": True,
        "strategy_efficacy_claim": False,
        "factor": args.factor,
        "top_n": args.top_n,
        "readiness": readiness,
        "metrics": summarize(result.nav),
        "trades": int(len(result.trades)),
        "warning": "Metrics validate plumbing only until research readiness is reached.",
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
