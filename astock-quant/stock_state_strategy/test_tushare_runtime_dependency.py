"""Catch a missing runtime dependency before stock-state Gold refresh reaches Tushare.

Runs under the same requirements.txt as the scheduled production workflow.
No provider credentials or network calls are needed.
"""

import importlib


def test_tushare_and_six_import_in_strategy_runtime():
    assert importlib.import_module("six") is not None
    assert importlib.import_module("tushare") is not None
