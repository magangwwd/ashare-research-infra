"""Offline regression tests for read-only stock personality uncertainty audit."""
import importlib.util
from pathlib import Path
import pandas as pd
import pytest

MODULE = Path(__file__).resolve().parents[1] / 'skills' / 'stock-personality-analysis' / 'uncertainty_audit.py'
spec = importlib.util.spec_from_file_location('uncertainty_audit', MODULE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

def test_wilson():
    lo, hi = audit.wilson(2, 10)
    assert 0 < lo < 20 < hi < 100
    assert audit.wilson(0, 0) is None

def test_bootstrap_deterministic_and_small_sample():
    assert audit.boot_mean_ci([-2,-1,1,2,3],seed=7,draws=100) == audit.boot_mean_ci([-2,-1,1,2,3],seed=7,draws=100)
    assert audit.boot_mean_ci([2]*4,draws=100) is None
    assert audit.boot_mean_ci([2]*5,draws=100) == [2.0,2.0]

def test_minute_reconciliation_and_duplicates():
    d = pd.DataFrame([dict(ts_code='600031.SH',trade_date='2026-10-13',
        open=10,high=12,low=9,close=11,vol=100,amount=1000)])
    m = pd.DataFrame([dict(ts_code='600031.SH',date='2026-10-13',
        trade_time='2026-10-13 09:30:00',open=10,high=12,low=9,
        close=11,vol=10000,amount=1000000)])
    r = audit.audit_minutes(d,m)
    assert r['ohlc_max_absolute_error'] == 0
    assert r['volume_ratio_minute_shares_to_daily_lots'] == [100,100]
    assert r['amount_ratio_minute_yuan_to_daily_thousand_yuan'] == [1000,1000]
    with pytest.raises(ValueError):
        audit.audit_minutes(d,pd.concat([m,m]))
