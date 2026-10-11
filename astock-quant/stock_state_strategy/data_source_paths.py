"""One canonical resolver for the shared Tushare Gold A-share data store.

Historical assets remain relative to the strategy project. Only the Gold
market-data path may be redirected with ASTOCK_MARKET_DATA_ROOT.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GOLD_SUFFIX = "down_data/data/gold/main_board"


def is_gold_market_path(value: str) -> bool:
    normalized = value.replace("\\", "/").rstrip("/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized.endswith(GOLD_SUFFIX)


def resolve_project_path(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not is_gold_market_path(value):
        return path.resolve() if path.is_absolute() else (base / path).resolve()

    override = os.environ.get("ASTOCK_MARKET_DATA_ROOT")
    if override:
        result = Path(os.path.expandvars(override)).expanduser()
        return result.resolve() if result.is_absolute() else (Path.cwd() / result).resolve()

    if path.is_absolute():
        return path.resolve()

    default = (base / path).resolve()
    if default.is_dir():
        return default

    # In astock-quant/stock_state_strategy, ../down_data differs from the
    # original Drive workspace layout project/{stock_state_strategy,down_data}.
    alternate = (ROOT.parent.parent / GOLD_SUFFIX).resolve()
    if alternate.is_dir():
        return alternate
    return default
