"""Kronos Return Top3 — entry point.

Usage (CPU-only recommended workflow):

  # Daily Top3 prediction — Kronos-small, full quality universe
  python -m kronos_return_top3 --predict-only --use-small --full-universe --output-prefix predict_small

  # Faster smoke test — Kronos-mini, 20 sampled stocks, 1 rebalance period
  python -m kronos_return_top3 --use-mini --sample-size 20 --hold-days 10 --max-periods 1 --output-prefix smoke_mini

  # Historical backtest — Kronos-small, 50 stocks (hours on CPU)
  python -m kronos_return_top3 --use-small --sample-size 50 --hold-days 10 --output-prefix sample50_small

Model guide (all three run locally; context length is trading days of lookback):
  Kronos-mini   4.1M   2048-bar context  fastest on CPU, lowest accuracy   --use-mini
  Kronos-small  24.7M   512-bar context  best speed/accuracy on CPU        --use-small
  Kronos-base  102.3M   512-bar context  highest accuracy, ~8x slower      (default)

Fine-tuning manifest (already generated; GPU training runs on cloud):
  python -m kronos_return_top3.prepare_finetune_data --lookback-window 512 --predict-window 10
"""
from kronos_return_top3.backtest import main

main()
