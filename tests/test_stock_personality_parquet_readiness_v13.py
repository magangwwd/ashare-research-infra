"""Offline synthetic Parquet tests: verify the existing CI pyarrow dependency."""
import importlib.util
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

MODULE = Path(__file__).resolve().parents[1] / "skills" / "stock-personality-analysis" / "parquet_readiness_v13.py"
spec = importlib.util.spec_from_file_location("parquet_readiness_v13", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def sample(tmp_path):
    path = tmp_path / "sample.parquet"
    table = pa.table({
        "ts_code": ["600031.SH", "600031.SH", "600000.SH"],
        "trade_date": pa.array(["2026-09-29", "2026-09-30", "2026-09-30"]),
        "adj_factor": [54.155, 54.155, 1.0],
    })
    pq.write_table(table, path, compression="zstd")
    return path


def test_parquet_roundtrip_and_symbol_filter(tmp_path):
    path = sample(tmp_path)
    result = module.audit_one(path)
    assert result["parquet_rows"] == 3
    assert result["symbol_rows"] == 2
    assert result["symbol_unique_dates"] == 2
    assert result["symbol_min_date"] == "2026-09-29"
    assert result["symbol_max_date"] == "2026-09-30"


def test_expected_sha256_guard(tmp_path):
    path = sample(tmp_path)
    expected = module.audit_one(path)["sha256"]
    assert module.audit_one(path, expected_sha256=expected)["sha256"] == expected
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        module.audit_one(path, expected_sha256="0" * 64)


def test_reject_missing_symbol_or_date_columns(tmp_path):
    path = tmp_path / "bad.parquet"
    pq.write_table(pa.table({"ts_code": ["600031.SH"]}), path)
    with pytest.raises(ValueError, match="Missing columns"):
        module.audit_one(path)
