"""Real Parquet diagnostic smoke tests; all fixtures are synthetic and isolated."""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
from tools.mainboard_real_parquet_shadow import diagnose


REL = "data/clean/daily/year=2026/month=10/part-000.parquet"


def _make(tmp_path, *, gold_close=10.5, gold_extra_future=False, gold_duplicate=False):
    t_path, g_path = tmp_path / "task.parquet", tmp_path / "gold.parquet"
    task_rows = [{
        "ts_code": "600031.SH", "trade_date": date(2026, 10, 23),
        "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
        "vol": 123.45, "amount": 67.89,
    }]
    gold_rows = [{
        "vendor_symbol": "600031.SH", "trade_date": date(2026, 10, 23),
        "open": 10.0, "high": 11.0, "low": 9.0, "close": gold_close,
        "volume_shares": 12345, "turnover_cny": 67890,
    }]
    if gold_extra_future:
        gold_rows.append(dict(gold_rows[0], trade_date=date(2026, 10, 30)))
    if gold_duplicate:
        gold_rows.append(dict(gold_rows[0]))
    pq.write_table(pa.Table.from_pylist(task_rows), t_path)
    pq.write_table(pa.Table.from_pylist(gold_rows), g_path)
    instruments_path = tmp_path / "instruments.parquet"
    instruments = [{
        "instrument_id": "CN.XSHG.600031", "vendor_symbol": "600031.SH",
        "board": "MAIN_BOARD", "exchange": "SSE", "currency": "CNY",
        "as_of_date": date(2026, 10, 30),
    }]
    pq.write_table(pa.Table.from_pylist(instruments), instruments_path)
    entry = {"path": REL, "bytes": t_path.stat().st_size, "sha256": hashlib.sha256(t_path.read_bytes()).hexdigest()}
    task_manifest = {
        "validated": True, "snapshot_as_of": "2026-10-24",
        "source_run_id": "sample", "file_count": 1,
        "total_bytes": entry["bytes"], "version": "synthetic",
        "files": [entry],
    }
    task_raw = json.dumps(task_manifest).encode("utf-8")
    pointer = {k: task_manifest[k] for k in ("validated", "snapshot_as_of", "source_run_id", "file_count", "total_bytes", "version")}
    pointer["manifest_sha256"] = hashlib.sha256(task_raw).hexdigest()
    gold_manifest = {
        "as_of_date": "2026-10-30",
        "validation": {"primary_keys_unique": True, "ohlc_valid": True},
        "files": {"instruments.parquet": {
            "rows": len(instruments),
            "sha256": hashlib.sha256(instruments_path.read_bytes()).hexdigest(),
        }},
    }
    return pointer, task_raw, gold_manifest, t_path, g_path


def run(tmp_path, **kwargs):
    p, m, gold, t, g = _make(tmp_path, **kwargs)
    return diagnose(p, m, gold, t, g, task_relative_path=REL, gold_instruments_path=t.parent / "instruments.parquet")


def test_real_parquet_same_values_diagnostic_not_production(tmp_path):
    report = run(tmp_path)
    assert report["status"] == "MATCHED_COMMON_ROWS_DIAGNOSTIC_ONLY"
    assert report["common_keys"] == 1
    assert report["numeric_field_difference_count"] == 0
    assert report["task01_file_sha256_verified_against_official_manifest"] is True
    assert report["gold_history_month_sha256_verified_against_official_manifest"] is False
    assert not report["five_trading_days_shadow_verified"]
    assert not report["production_switch_allowed"]
    assert not report["gold_historical_pit_universe_verified"]


def test_real_parquet_detects_price_difference(tmp_path):
    report = run(tmp_path, gold_close=10.6)
    assert report["status"] == "DIFFERENCES_FOUND"
    assert report["numeric_field_difference_count"] == 1
    assert report["numeric_field_differences"][0]["field"] == "close"


def test_extra_gold_future_rows_not_counted_as_missing(tmp_path):
    report = run(tmp_path, gold_extra_future=True)
    assert report["gold_month_rows"] == 2
    assert report["gold_rows_after_cutoff"] == 1
    assert report["only_in_gold"] == 0
    assert report["common_cutoff"] == "2026-10-24"


def test_turnover_nearest_yuan_attribution_does_not_erase_difference(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    rows = pq.read_table(t).to_pylist()
    rows[0]["amount"] = 67.8904
    pq.write_table(pa.Table.from_pylist(rows), t)
    item = {"path": REL, "bytes": t.stat().st_size,
            "sha256": hashlib.sha256(t.read_bytes()).hexdigest()}
    doc = json.loads(m)
    doc["files"] = [item]
    doc["total_bytes"] = item["bytes"]
    m = json.dumps(doc).encode()
    p["total_bytes"] = item["bytes"]
    p["manifest_sha256"] = hashlib.sha256(m).hexdigest()
    report = diagnose(p, m, gold, t, g, task_relative_path=REL, gold_instruments_path=t.parent / "instruments.parquet")
    assert report["numeric_field_difference_count"] == 1
    assert report["field_difference_counts"]["turnover_cny"] == 1
    assert report["field_difference_counts"]["close"] == 0
    assert report["turnover_nearest_yuan_compatible_differences"] == 1
    assert report["turnover_nearest_yuan_incompatible_differences"] == 0
    assert report["status"] == "DIFFERENCES_FOUND"
    assert not report["production_switch_allowed"]


def test_half_yuan_tie_separates_half_even_from_half_up(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    rows = pq.read_table(t).to_pylist()
    rows[0]["amount"] = 67.8905
    pq.write_table(pa.Table.from_pylist(rows), t)
    item = {"path": REL, "bytes": t.stat().st_size,
            "sha256": hashlib.sha256(t.read_bytes()).hexdigest()}
    doc = json.loads(m)
    doc["files"] = [item]
    doc["total_bytes"] = item["bytes"]
    m = json.dumps(doc).encode()
    p["total_bytes"] = item["bytes"]
    p["manifest_sha256"] = hashlib.sha256(m).hexdigest()
    report = diagnose(p, m, gold, t, g, task_relative_path=REL, gold_instruments_path=t.parent / "instruments.parquet")
    assert report["numeric_field_difference_count"] == 1
    assert report["turnover_nearest_yuan_compatible_differences"] == 0
    assert report["turnover_nearest_yuan_half_even_compatible_differences"] == 1
    assert report["turnover_absolute_difference_le_half_yuan"] == 1
    assert report["turnover_absolute_difference_gt_half_yuan"] == 0
    assert report["turnover_exact_half_yuan_difference_count"] == 1
    exception = report["turnover_half_even_incompatible_examples"][0]
    assert exception["vendor_symbol"] == "600031.SH"
    assert exception["trade_date"] == "2026-10-23"
    assert exception["task01_turnover_cny"] == "67890.5000"
    assert exception["gold_turnover_cny"] == "67890"
    assert exception["half_up_yuan"] == "67891"
    assert exception["half_even_yuan"] == "67890"
    assert exception["absolute_difference_cny"] == "0.5000"
    assert not report["production_switch_allowed"]


def test_turnover_incompatible_examples_are_bounded_by_sample_limit(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    task_rows = pq.read_table(t).to_pylist()
    gold_rows = pq.read_table(g).to_pylist()
    # Gold stores HALF_UP rather than HALF_EVEN at exactly half-yuan ties.
    for i in range(2):
        task_rows.append(dict(task_rows[0], ts_code=f"60003{i+2}.SH", amount=67.8905))
        gold_rows.append(dict(gold_rows[0], vendor_symbol=f"60003{i+2}.SH", turnover_cny=67891))
    task_rows[0]["amount"] = 67.8905
    gold_rows[0]["turnover_cny"] = 67891
    pq.write_table(pa.Table.from_pylist(task_rows), t)
    pq.write_table(pa.Table.from_pylist(gold_rows), g)
    inst = t.parent / "instruments.parquet"
    inst_rows = pq.read_table(inst).to_pylist()
    for i in range(2):
        symbol=f"60003{i+2}.SH"
        inst_rows.append(dict(inst_rows[0], vendor_symbol=symbol, instrument_id="CN.XSHG."+symbol[:6]))
    pq.write_table(pa.Table.from_pylist(inst_rows), inst)
    gold["files"]["instruments.parquet"]["rows"] = len(inst_rows)
    gold["files"]["instruments.parquet"]["sha256"] = hashlib.sha256(inst.read_bytes()).hexdigest()
    item = {"path": REL, "bytes": t.stat().st_size,
            "sha256": hashlib.sha256(t.read_bytes()).hexdigest()}
    doc = json.loads(m)
    doc["files"] = [item]
    doc["total_bytes"] = item["bytes"]
    m = json.dumps(doc).encode()
    p["total_bytes"] = item["bytes"]
    p["manifest_sha256"] = hashlib.sha256(m).hexdigest()
    report = diagnose(p, m, gold, t, g, task_relative_path=REL,
                      gold_instruments_path=inst, max_examples=1)
    assert report["turnover_nearest_yuan_half_even_compatible_differences"] == 0
    assert report["numeric_field_difference_count"] == 3
    assert len(report["turnover_half_even_incompatible_examples"]) == 1
    assert report["turnover_half_even_incompatible_examples"][0]["vendor_symbol"] == "600031.SH"
    assert report["production_switch_allowed"] is False


def test_material_turnover_discrepancy_not_explained_by_rounding(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    rows = pq.read_table(g).to_pylist()
    rows[0]["turnover_cny"] = 67892
    pq.write_table(pa.Table.from_pylist(rows), g)
    report = diagnose(p, m, gold, t, g, task_relative_path=REL, gold_instruments_path=t.parent / "instruments.parquet")
    assert report["field_difference_counts"]["turnover_cny"] == 1
    assert report["turnover_nearest_yuan_incompatible_differences"] == 1
    assert report["turnover_nearest_yuan_compatible_differences"] == 0
    assert report["turnover_absolute_difference_gt_half_yuan"] == 1
    assert report["turnover_absolute_difference_le_half_yuan"] == 0


def test_task_only_prefix_breakdown_does_not_certify_missing_gold(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    rows = pq.read_table(t).to_pylist()
    rows.append(dict(rows[0], ts_code="300001.SZ"))
    pq.write_table(pa.Table.from_pylist(rows), t)
    item = {"path": REL, "bytes": t.stat().st_size,
            "sha256": hashlib.sha256(t.read_bytes()).hexdigest()}
    doc = json.loads(m)
    doc["files"] = [item]
    doc["total_bytes"] = item["bytes"]
    m = json.dumps(doc).encode()
    p["total_bytes"] = item["bytes"]
    p["manifest_sha256"] = hashlib.sha256(m).hexdigest()
    report = diagnose(p, m, gold, t, g, task_relative_path=REL, gold_instruments_path=t.parent / "instruments.parquet")
    assert report["only_in_task01"] == 0
    assert report["task_non_mainboard_rows_excluded"] == 1
    assert report["task_non_mainboard_excluded_by_prefix"]["300.SZ"] == 1
    assert not report["gold_historical_pit_universe_verified"]


def test_duplicate_gold_primary_key_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="duplicate symbol/date"):
        run(tmp_path, gold_duplicate=True)


def test_task_file_modification_blocks_before_read(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    t.write_bytes(t.read_bytes() + b"X")
    with pytest.raises(ValueError, match="SHA/size mismatch"):
        diagnose(p, m, gold, t, g, task_relative_path=REL, gold_instruments_path=t.parent / "instruments.parquet")


def test_manifest_tampering_blocks_before_read(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    tampered = m.replace(b"2026-10-24", b"2026-10-25")
    with pytest.raises(ValueError, match="SHA mismatch"):
        diagnose(p, tampered, gold, t, g, task_relative_path=REL, gold_instruments_path=t.parent / "instruments.parquet")


def test_unverified_gold_manifest_blocks(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    gold["validation"]["ohlc_valid"] = False
    with pytest.raises(ValueError, match="validation failed"):
        diagnose(p, m, gold, t, g, task_relative_path=REL, gold_instruments_path=t.parent / "instruments.parquet")


def test_wrong_task_clean_path_blocks(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    with pytest.raises(ValueError, match="clean daily"):
        diagnose(p, m, gold, t, g, task_relative_path="data/raw/daily/year=2026/month=10/part-000.parquet", gold_instruments_path=t.parent / "instruments.parquet")



def test_main_board_only_excludes_other_markets_not_gold_missing(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    rows = pq.read_table(t).to_pylist()
    for symbol in ("300001.SZ", "301001.SZ", "302001.SZ",
                   "688001.SH", "689001.SH", "920001.BJ"):
        rows.append(dict(rows[0], ts_code=symbol))
    pq.write_table(pa.Table.from_pylist(rows), t)
    entry = {"path": REL, "bytes": t.stat().st_size,
             "sha256": hashlib.sha256(t.read_bytes()).hexdigest()}
    doc = json.loads(m)
    doc["files"] = [entry]
    doc["total_bytes"] = entry["bytes"]
    m = json.dumps(doc).encode()
    p["total_bytes"] = entry["bytes"]
    p["manifest_sha256"] = hashlib.sha256(m).hexdigest()
    report = diagnose(p, m, gold, t, g, task_relative_path=REL,
                      gold_instruments_path=t.parent / "instruments.parquet")
    assert report["universe_scope"] == "SSE_SZSE_A_SHARE_MAIN_BOARD_ONLY"
    assert report["task_non_mainboard_rows_excluded"] == 6
    assert report["common_keys"] == 1
    assert report["only_in_task01"] == 0
    assert not report["gold_historical_pit_universe_verified"]
    assert not report["production_switch_allowed"]


def test_gold_board_member_and_sha_required(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    inst = t.parent / "instruments.parquet"
    rows = pq.read_table(inst).to_pylist()
    rows[0]["board"] = "GEM"
    pq.write_table(pa.Table.from_pylist(rows), inst)
    gold["files"]["instruments.parquet"]["sha256"] = hashlib.sha256(inst.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="non-main-board"):
        diagnose(p, m, gold, t, g, task_relative_path=REL,
                 gold_instruments_path=inst)
    gold["files"]["instruments.parquet"]["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="instruments SHA mismatch"):
        diagnose(p, m, gold, t, g, task_relative_path=REL,
                 gold_instruments_path=inst)


def test_main_board_only_rejects_non_cny_gold_instrument(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    inst = t.parent / "instruments.parquet"
    rows = pq.read_table(inst).to_pylist()
    rows[0]["currency"] = "USD"
    pq.write_table(pa.Table.from_pylist(rows), inst)
    gold["files"]["instruments.parquet"]["sha256"] = hashlib.sha256(inst.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="non-main-board A-share"):
        diagnose(p, m, gold, t, g, task_relative_path=REL,
                 gold_instruments_path=inst)


def test_gold_month_cannot_contain_non_mainboard_symbol(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    rows = pq.read_table(g).to_pylist()
    rows.append(dict(rows[0], vendor_symbol="300001.SZ"))
    pq.write_table(pa.Table.from_pylist(rows), g)
    with pytest.raises(ValueError, match="outside verified main-board"):
        diagnose(p, m, gold, t, g, task_relative_path=REL,
                 gold_instruments_path=t.parent / "instruments.parquet")


def test_mainboard_membership_missing_blocks_instead_of_broad_market(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    with pytest.raises(ValueError, match="verified Gold instruments required"):
        diagnose(p, m, gold, t, g, task_relative_path=REL)

def test_bad_pointer_version_rejected(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    p["version"] = "another"
    with pytest.raises(ValueError, match="pointer/manifest mismatch"):
        diagnose(p, m, gold, t, g, task_relative_path=REL, gold_instruments_path=t.parent / "instruments.parquet")


def test_no_overlapping_keys_is_not_success(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    rows = pq.read_table(g).to_pylist()
    # Use the same authenticated main-board security, but a different date.
    # Changing the symbol would correctly fail the stronger universe gate first.
    rows[0]["trade_date"] = date(2026, 10, 22)
    pq.write_table(pa.Table.from_pylist(rows), g)
    with pytest.raises(ValueError, match="no overlapping"):
        diagnose(p, m, gold, t, g, task_relative_path=REL, gold_instruments_path=t.parent / "instruments.parquet")


def test_future_task_record_outside_cutoff_not_verified(tmp_path):
    p, m, gold, t, g = _make(tmp_path)
    rows = pq.read_table(t).to_pylist()
    rows.append(dict(rows[0], trade_date=date(2026, 10, 30)))
    pq.write_table(pa.Table.from_pylist(rows), t)
    entry = {
        "path": REL, "bytes": t.stat().st_size,
        "sha256": hashlib.sha256(t.read_bytes()).hexdigest(),
    }
    doc = json.loads(m)
    doc["files"] = [entry]
    doc["total_bytes"] = entry["bytes"]
    m = json.dumps(doc).encode("utf-8")
    p["total_bytes"] = entry["bytes"]
    p["manifest_sha256"] = hashlib.sha256(m).hexdigest()
    result = diagnose(p, m, gold, t, g, task_relative_path=REL, gold_instruments_path=t.parent / "instruments.parquet")
    assert result["task_rows_after_cutoff"] == 1
    assert result["task_rows_at_or_before_cutoff"] == 1
    assert not result["five_trading_days_shadow_verified"]
