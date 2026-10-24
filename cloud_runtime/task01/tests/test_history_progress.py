from datetime import date

import pandas as pd
import pytest

from data_pipeline import clean
from data_pipeline.manifest import Store, atomic_json, read_dataset, write_compacted
from data_pipeline.validate import history_progress, progress_markdown, validate_task01


@pytest.fixture
def progress_source(tmp_path, config, provider, client):
    store = Store(tmp_path, config)
    stage = store.begin()
    store.save("stock_basic", provider.stocks)
    master = clean.security_master(provider.stocks, "2026-09-04T12:00:00Z")
    store.save_clean("security_master", clean.normalize("security_master", master))
    for exchange in ("SSE", "SZSE"):
        store.save("trade_cal", client.fetch("trade_cal", exchange=exchange,
                                           start_date="20260901", end_date="20260904"))
    for name in ("daily", "adj_factor", "daily_basic"):
        for day in ("20260901", "20260904"):
            store.save(name, client.fetch(name, trade_date=day))
    # A fake complete label must not hide the two interior missing trading sessions.
    atomic_json(tmp_path / "metadata/manifest/history.json", {"all_enabled_requests_examined": True})
    store.receipt("daily", {"trade_date": "20260902"}, 20)
    capabilities = {"interfaces": {name: {"status": "AVAILABLE"} for name in ("daily", "adj_factor", "daily_basic")}}
    atomic_json(tmp_path / "metadata/capabilities/latest.json", capabilities)
    return tmp_path, store, stage, config


def inspect(source):
    root, store, stage, config = source
    acceptance = validate_task01(stage, config, date(2026, 9, 4))
    assert acceptance["passed"], acceptance["errors"]
    return history_progress(stage, root / "metadata", config, date(2026, 9, 4), acceptance)


def test_interior_missing_dates_cannot_be_hidden_by_range_or_receipts(progress_source):
    report = inspect(progress_source)
    daily = report["datasets"]["daily"]
    assert daily["layers"]["clean"]["date_ranges"]["trade_date"]["distinct_dates"] == 2
    assert daily["history"]["expected_sessions"] == 4
    assert daily["history"]["missing_ranges"] == [{"start_date": "2026-09-02", "end_date": "2026-09-03", "sessions": 2}]
    assert report["stored_critical_validation_passed"]
    assert not report["core_observed_range_complete"]
    assert report["history_run"]["all_enabled_requests_examined"]
    assert "daily" in progress_markdown(report)


def test_observed_provider_prefix_is_distinct_from_pending_downloads(progress_source):
    root, _, _, _ = progress_source
    atomic_json(root / "metadata/capabilities/history_ranges.json", {
        "daily": {"first_observed_date": "20260903"}})
    item = inspect(progress_source)["datasets"]["daily"]["history"]
    assert item["target_start"] == "2026-09-03"
    assert item["provider_prefix_sessions"] == 2
    assert item["missing_sessions"] == 1
    assert item["missing_ranges"][0]["start_date"] == "2026-09-03"


def test_permission_failure_and_empty_query_do_not_prove_extension_history(progress_source):
    root, store, _, config = progress_source
    config["datasets"]["required"] = True
    atomic_json(root / "metadata/capabilities/latest.json", {"interfaces": {
        "sw_daily": {"status": "UNAVAILABLE", "permission_status": "DENIED", "reason": "permission denied"}}})
    store.receipt("income", {"ts_code": "600000.SH", "start_date": "20260101"}, 0)
    store.gap("sw_daily", {"trade_date": "20260904"}, "permission denied")
    report = inspect(progress_source)
    assert report["datasets"]["income"]["query_receipts"]["empty_responses"] == 1
    assert report["datasets"]["income"]["status"] == "NOT_STORED"
    assert report["datasets"]["sw_daily"]["status"] == "CAPABILITY_UNAVAILABLE"
    assert report["datasets"]["sw_daily"]["gaps"][0]["reason"] == "permission denied"
    assert report["extensions_history_complete"] is None


def test_full_core_range_requires_all_actual_dates_and_valid_acceptance(progress_source, client):
    root, store, stage, config = progress_source
    for name in ("daily", "adj_factor", "daily_basic"):
        for day in ("20260902", "20260903"):
            store.save(name, client.fetch(name, trade_date=day))
    report = inspect(progress_source)
    assert report["core_observed_range_complete"]
    assert report["full_requested_core_range_complete"]
    acceptance = validate_task01(stage, config, date(2026, 9, 4))
    acceptance["passed"] = False
    assert not history_progress(stage, root / "metadata", config, date(2026, 9, 4), acceptance)["core_observed_range_complete"]


def test_file_inventory_survives_corrupt_manifest(progress_source):
    root, _, _, _ = progress_source
    (root / "metadata/manifest/requests.json").write_text("corrupted")
    (root / "metadata/manifest/partitions.json").write_text("corrupted")
    report = inspect(progress_source)
    assert report["datasets"]["daily"]["layers"]["clean"]["rows"] == 40
    assert report["datasets"]["daily"]["history"]["stored_target_sessions"] == 2


def test_missing_calendar_day_does_not_make_history_look_complete(progress_source, client):
    _, store, stage, config = progress_source
    for name in ("daily", "adj_factor", "daily_basic"):
        for day in ("20260902", "20260903"):
            store.save(name, client.fetch(name, trade_date=day))
    calendar = read_dataset(stage, "clean", "trade_cal")
    calendar = calendar.loc[~(calendar["exchange"].eq("SZSE") & calendar["calendar_date"].eq(date(2026, 9, 2)))]
    write_compacted(stage / "clean/trade_cal/year=2026", calendar, "trade_cal", "clean", 192)
    report = inspect(progress_source)
    assert report["datasets"]["daily"]["history"]["missing_sessions"] == 0
    assert report["calendar_coverage"]["SZSE"]["missing_calendar_days"] == 1
    assert not report["core_observed_range_complete"]
