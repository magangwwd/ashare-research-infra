from datetime import date

import pandas as pd
import pytest
import pyarrow as pa

from data_pipeline import clean
from data_pipeline.manifest import Store, partitions, read_dataset


def test_date_conversion_and_invalid_dates():
    assert clean.convert_dates(pd.Series(["20261002", "2026-09-30", None, ""])).tolist()[:2] == [date(2026,10,2), date(2026,9,30)]
    with pytest.raises(ValueError):
        clean.convert_dates(pd.Series(["20260230"]))


def test_explicit_schema_dedup_and_new_fields(provider):
    rows = pd.DataFrame([{"ts_code": "600000.SH", "trade_date": "20261002", "close": 10, "provider_added": 99},
                         {"ts_code": "600000.SH", "trade_date": "20261002", "close": 11, "provider_added": 100}])
    result = clean.normalize("daily", rows)
    assert len(result) == 1 and result.iloc[0]["close"] == 11
    assert "provider_added" not in result and result.iloc[0]["trade_date"] == date(2026,10,2)
    assert clean.arrow_schema("daily").field("trade_date").type == pa.date32()
    assert clean.security_id("600000.SH") == clean.security_id("600000.SH")
    assert clean.security_id("600000.SH") != clean.security_id("600000.SZ")


def test_mainboard_uses_market_metadata(provider):
    rows = provider.stocks.iloc[:2].copy()
    rows.loc[rows.index[0], "market"] = "科创板"
    master = clean.normalize("security_master", clean.security_master(rows, "2026-10-02T12:00:00Z"))
    assert clean.mainboard_pool(master)["ts_code"].tolist() == ["600001.SH"]


def test_partition_generation_and_financial_revisions(tmp_path, config):
    store = Store(tmp_path, config)
    stage = store.begin()
    rows = pd.DataFrame([{"ts_code": "600000.SH", "end_date": "20251231", "ann_date": "20260301", "update_flag": "0", "roe": 10},
                         {"ts_code": "600000.SH", "end_date": "20251231", "ann_date": "20260402", "update_flag": "1", "roe": 12}])
    assert partitions("fina_indicator", rows.iloc[:1], "raw")[0][0] == "ann_year=2026/ann_month=03"
    normalized = clean.normalize("fina_indicator", rows)
    assert partitions("fina_indicator", normalized, "clean")[0][0] == "report_year=2025/report_period=20251231"
    store.save("fina_indicator", rows.iloc[:1])
    store.save("fina_indicator", rows.iloc[1:])
    assert len(read_dataset(stage, "raw", "fina_indicator")) == 2
    result = read_dataset(stage, "clean", "fina_indicator")
    assert len(result) == 1 and result.iloc[0]["roe"] == 12
    # An older announcement arriving later must not replace the latest revision.
    store.save("fina_indicator", rows.iloc[:1])
    assert read_dataset(stage, "clean", "fina_indicator").iloc[0]["roe"] == 12


def test_index_metadata_dates_are_typed_without_changing_critical_versions():
    raw = pd.DataFrame([{"ts_code": "000300.SH", "base_date": "20041231", "list_date": "20050408", "base_point": 1000}])
    result = clean.normalize("index_basic", raw)
    assert result.iloc[0]["base_date"] == date(2004,12,31)
    assert clean.arrow_schema("index_basic").field("list_date").type == pa.date32()
    assert result.iloc[0]["schema_version"] == "2.0.0"
    assert clean.schema_version("daily") == "1.0.0"


def test_theoretical_pool_respects_actual_exchange_calendar(provider):
    rows = provider.stocks.copy()
    rows.loc[rows.index[:5], "exchange"] = "SZSE"
    master = clean.normalize("security_master", clean.security_master(rows, "2026-10-02T12:00:00Z"))
    assert len(clean.expected_pool(master, date(2026,10,2), open_exchanges={"SSE"})) == 15
    assert len(clean.expected_pool(master, date(2026,10,2), open_exchanges={"SSE","SZSE"})) == 20


def test_newly_fetched_old_market_raw_is_retained(tmp_path, config):
    from datetime import datetime
    store = Store(tmp_path, config)
    stage = store.begin()
    store.save("daily", pd.DataFrame([{"ts_code": "600000.SH", "trade_date": "19901219", "close": 10}]))
    store.retain_raw(datetime.now())
    assert len(read_dataset(stage, "raw", "daily")) == 1
