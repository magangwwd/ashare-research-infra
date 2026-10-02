from pathlib import Path

import pandas as pd
import pytest

from data_pipeline import clean
from data_pipeline.manifest import Store, read_json, request_id
from data_pipeline.run_task01 import Pipeline
from data_pipeline.validate import ValidationError, validate_task01


def test_manifest_rebuild_after_corruption(tmp_path, config, provider):
    store = Store(tmp_path, config)
    stage = store.begin()
    store.save("stock_basic", provider.stocks)
    store.receipt("stock_basic", {"list_status": "L"}, len(provider.stocks))
    store.gap("daily", {"trade_date": "20261002"}, "interrupted")
    store.scan()
    (store.metadata / "partitions.json").write_text("corrupt")
    rebuilt = store.scan()
    assert any(e["dataset"] == "stock_basic" and e["rows"] == 20 for e in rebuilt)
    assert read_json(store.metadata / "gaps.json")
    assert read_json(store.metadata / "requests.json")[request_id("stock_basic", {"list_status": "L"})]["status"] == "complete"
    resumed = Store(tmp_path, config)
    assert resumed.begin() == stage


def test_full_offline_pipeline_and_failure_keeps_production(tmp_path, config, client, provider, as_of):
    report = Pipeline(tmp_path, config, client, as_of).run()
    assert report["passed"] and report["mode"] == "DAILY_UPDATE"
    previous = (tmp_path / "data/current").resolve()
    content = {p.relative_to(previous): p.read_bytes() for p in previous.rglob("*.parquet")}
    provider.fail.add("daily")
    with pytest.raises(Exception, match="Critical capability audit FAILED"):
        Pipeline(tmp_path, config, client, as_of).run()
    assert (tmp_path / "data/current").resolve() == previous
    assert all((previous / p).read_bytes() == value for p, value in content.items())
    assert not validate_task01(tmp_path / "absent", config, as_of)["passed"]


def test_optional_failure_does_not_fail_critical(tmp_path, config, client, as_of):
    config["datasets"]["optional"] = True
    report = Pipeline(tmp_path, config, client, as_of).run()
    assert report["passed"]
    assert any(v["dataset"] == "margin" for v in read_json(tmp_path / "metadata/manifest/gaps.json").values())


def test_exact_95_percent_and_below_threshold(tmp_path, config, client, provider, as_of):
    provider.missing["daily"] = {"600000.SH"}
    assert Pipeline(tmp_path, config, client, as_of).run()["passed"]
    pointer = (tmp_path / "data/current").resolve()
    # Use a new date to ensure old validated rows cannot fill a newly missing session.
    from datetime import timedelta
    provider.missing["daily"] = {"600000.SH", "600001.SH"}
    with pytest.raises(ValidationError, match="coverage=90.00%"):
        Pipeline(tmp_path, config, client, as_of + timedelta(days=3)).run()
    assert (tmp_path / "data/current").resolve() == pointer


def test_missing_stock_is_retried_individually(tmp_path, config, client, provider, as_of):
    original = provider.query

    def query(endpoint, **params):
        frame = original(endpoint, **params)
        if endpoint == "daily" and "ts_code" not in params:
            return frame.loc[frame["ts_code"] != "600000.SH"]
        return frame

    provider.query = query
    assert Pipeline(tmp_path, config, client, as_of).run()["passed"]
    assert any(n == "daily" and p.get("ts_code") == "600000.SH" for n, p in provider.calls)


def test_old_rows_cannot_mask_new_incomplete_response(tmp_path, config, client, provider, as_of):
    Pipeline(tmp_path, config, client, as_of).run()
    previous = (tmp_path / "data/current").resolve()
    provider.missing["daily"] = {"600000.SH", "600001.SH"}
    with pytest.raises(ValidationError, match="coverage=90.00%"):
        Pipeline(tmp_path, config, client, as_of).run()
    assert (tmp_path / "data/current").resolve() == previous


def test_history_resume_does_not_redownload_complete_dates(tmp_path, config, client, provider, as_of):
    config["download"]["history_requests_per_run"] = 3
    config["download"]["maintenance_history_requests_per_run"] = 3
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.bootstrap()
    pipeline.history()
    units = {(r['dataset'], r['params']['trade_date']) for r in pipeline.store.receipts.values()
             if r['dataset'] in ('daily', 'adj_factor', 'daily_basic') and 'ts_code' not in r['params']}
    assert len(units) == 3
    before = {(name, day): sum(n == name and p.get('trade_date') == day for n, p in provider.calls)
              for name, day in units}
    resumed = Pipeline(tmp_path, config, client, as_of)
    resumed.capabilities, resumed.master, resumed.sessions = pipeline.capabilities, pipeline.master, pipeline.sessions
    resumed.history()
    after = {(name, day): sum(n == name and p.get('trade_date') == day for n, p in provider.calls)
             for name, day in units}
    assert before == after
    assert read_json(tmp_path / "metadata/manifest/history.json")["fetched_this_run"] == 3


def test_maintenance_and_smoke_are_real_complete_paths(tmp_path, config, client, as_of):
    from datetime import timedelta
    report = Pipeline(tmp_path, config, client, as_of + timedelta(days=1)).run(sample=True)
    assert report["passed"] and report["mode"] == "MAINTENANCE"
    assert report["datasets"]["security_master"]["rows"] == 2


def test_failed_validation_cannot_publish(tmp_path, config):
    store = Store(tmp_path, config)
    store.begin()
    with pytest.raises(ValueError, match="passed"):
        store.publish({"passed": False})
    assert not (tmp_path / "data/current").exists()


def test_checkpoint_roundtrip_preserves_production_and_resumes(tmp_path, config, client, as_of):
    import tarfile
    from data_pipeline.manifest import read_dataset
    root = tmp_path / "runner_one"
    Pipeline(root, config, client, as_of).run()
    unfinished = Store(root, config)
    staged = unfinished.begin()
    params = {"trade_date": "20261002"}
    unfinished.gap("daily", params, "simulated interrupted run")
    archive = tmp_path / "checkpoint.tar.gz"
    with tarfile.open(archive, "w:gz", dereference=False) as stream:
        stream.add(root / "data", arcname="data")
        stream.add(root / "metadata", arcname="metadata")
    restored = tmp_path / "runner_two"
    restored.mkdir()
    with tarfile.open(archive) as stream:
        stream.extractall(restored, filter="data")
    recovered = Store(restored, config)
    assert recovered.begin().name == staged.name
    assert (restored / "data/clean").is_dir()
    assert len(read_dataset(restored / "data/current", "clean", "stock_basic")) == 20
    assert recovered.gaps[request_id("daily", params)]["reason"] == "simulated interrupted run"


def test_pointer_switch_failure_keeps_previous_version(tmp_path, config, client, as_of, monkeypatch):
    import os
    Pipeline(tmp_path, config, client, as_of).run()
    previous = (tmp_path / "data/current").resolve()
    store = Store(tmp_path, config)
    store.begin()
    replace = os.replace

    def failed_switch(source, target):
        if Path(target) == tmp_path / "data/current":
            raise OSError("simulated pointer failure")
        return replace(source, target)

    monkeypatch.setattr(os, "replace", failed_switch)
    with pytest.raises(OSError, match="pointer failure"):
        store.publish({"passed": True})
    assert (tmp_path / "data/current").resolve() == previous


def test_monthly_audit_respects_actual_api_history_floor(tmp_path, config, client, provider, as_of):
    from datetime import date
    from data_pipeline.manifest import atomic_json
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.master = clean.normalize("security_master", clean.security_master(provider.stocks, "2026-10-02T12:00:00Z"))
    pipeline.sessions = [date(2001,1,2), date(2005,1,3)] + [date(2026,10,2)] * 15
    config["download"].update(history_start="19900101", audit_requests_per_run=10)
    atomic_json(tmp_path / "metadata/capabilities/history_ranges.json", {"daily_basic": {"first_observed_date": "20040101"}})
    fetched = []
    pipeline.daily = lambda name, day: fetched.append((name, day))
    pipeline.monthly_audit()
    assert ("daily_basic", date(2001,1,2)) not in fetched
    assert ("daily_basic", date(2005,1,3)) in fetched


def test_retired_stock_missing_market_is_preserved_and_recorded(tmp_path, config, client, provider, as_of):
    from data_pipeline.manifest import read_dataset
    retired = provider.stocks.iloc[:1].copy()
    retired["ts_code"], retired["symbol"] = "T600018.SH", "T600018"
    retired["market"], retired["list_status"], retired["delist_date"] = None, "D", "20061020"
    provider.stocks = pd.concat([provider.stocks, retired], ignore_index=True)
    report = Pipeline(tmp_path, config, client, as_of).run()
    assert report["passed"]
    master = read_dataset(tmp_path / "data/current", "clean", "security_master")
    assert master.loc[master["ts_code"] == "T600018.SH", "board"].iloc[0] == "UNKNOWN"
    assert any(g["params"].get("metadata_field") == "market" for g in read_json(tmp_path / "metadata/manifest/gaps.json").values())


def test_listed_stock_missing_market_still_blocks_publication(tmp_path, config, client, provider, as_of):
    provider.stocks.loc[0, "market"] = None
    with pytest.raises(ValidationError, match="listed securities have missing market"):
        Pipeline(tmp_path, config, client, as_of).run()
    assert not (tmp_path / "data/current").exists()


def test_calendar_leading_empty_history_is_an_observed_boundary(tmp_path, config, client, provider, as_of):
    config["download"]["history_start"] = "20240101"
    original = provider.query

    def query(endpoint, **params):
        frame = original(endpoint, **params)
        if endpoint == "trade_cal" and params.get("exchange") == "SZSE" and params.get("start_date") == "20240101":
            return frame.iloc[:0]
        return frame

    provider.query = query
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.bootstrap()
    ranges = read_json(tmp_path / "metadata/capabilities/calendar_ranges.json")
    assert ranges["SZSE"]["first_observed_date"] == "2025-01-01"
    assert ranges["SZSE"]["empty_leading_years"] == [2024]
    assert not any(g["dataset"] == "trade_cal" for g in pipeline.store.gaps.values())


def test_calendar_interior_gap_still_blocks(tmp_path, config, client, provider, as_of):
    config["download"]["history_start"] = "20230101"
    original = provider.query

    def query(endpoint, **params):
        frame = original(endpoint, **params)
        if endpoint == "trade_cal" and params.get("exchange") == "SZSE" and params.get("start_date") == "20240101":
            return frame.iloc[:0]
        return frame

    provider.query = query
    with pytest.raises(ValidationError, match="interior calendar gap"):
        Pipeline(tmp_path, config, client, as_of).run()
    assert not (tmp_path / "data/current").exists()


def test_stock_metadata_repair_requests_essential_source_fields(tmp_path, config, client, provider, as_of):
    retired = provider.stocks.iloc[:1].copy()
    retired["ts_code"], retired["symbol"] = "T600018.SH", "T600018"
    retired["market"], retired["list_status"], retired["delist_date"] = None, "D", "20061020"
    provider.stocks = pd.concat([provider.stocks, retired], ignore_index=True)
    config["download"]["repair_requests_per_run"] = 15
    assert Pipeline(tmp_path, config, client, as_of).run()["passed"]
    repairs = [p for n,p in provider.calls if n == "stock_basic" and p.get("ts_code") == "T600018.SH"]
    assert repairs and all("list_status" in p["fields"] for p in repairs)


def test_rebuilt_partition_removes_resolved_gap(tmp_path, config, provider):
    store = Store(tmp_path, config)
    store.begin()
    store.save("stock_basic", provider.stocks)
    params = {"partition": "snapshot=current", "layer": "clean"}
    store.gap("stock_basic", params, "corrupt partition was rebuilt")
    store.scan()
    assert request_id("stock_basic", params) not in store.gaps


def test_history_boundary_requires_coverage_not_just_nonempty(tmp_path, config, client, provider, as_of):
    original = provider.query

    def query(endpoint, **params):
        frame = original(endpoint, **params)
        if endpoint == "daily" and params.get("trade_date", "99999999") < "20260910":
            return frame.loc[frame["ts_code"] == "600000.SH"]
        return frame

    provider.query = query
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.bootstrap()
    pipeline.discover_history_ranges()
    ranges = read_json(tmp_path / "metadata/capabilities/history_ranges.json")
    assert ranges["daily"]["first_observed_date"] == "20260910"
    assert ranges["daily"]["coverage_checked"]
    assert any(g["params"].get("coverage_boundary") for g in pipeline.store.gaps.values())


def test_bad_historical_coverage_blocks_acceptance_even_if_latest_is_good(tmp_path, config, client, provider, as_of):
    from datetime import date
    Pipeline(tmp_path, config, client, as_of).run()
    store = Store(tmp_path, config)
    stage = store.begin()
    raw = provider.query("daily", trade_date="20260901").iloc[:1]
    store.save("daily", raw, replace_dates={date(2026,9,1)})
    report = validate_task01(stage, config, as_of)
    assert not report["passed"]
    assert any("2026-09-01" in e for e in report["errors"])
    assert any(e["dataset"] == "daily" and e["validation_status"] == "failed" for e in store.scan())


def test_industry_history_includes_later_listings_and_exited_members(tmp_path, config, client, provider, as_of):
    from data_pipeline.manifest import read_dataset
    original = provider.query

    def query(endpoint, **params):
        if endpoint == "index_member_all":
            state = params.get("is_new", "Y")
            return pd.DataFrame([{"ts_code": params["ts_code"], "l3_code": "850001.SI", "is_new": state,
                                  "in_date": "20000101" if state == "Y" else "19990101",
                                  "out_date": None if state == "Y" else "20000101"}])
        return original(endpoint, **params)

    provider.query = query
    config["download"]["history_start"] = "19900101"
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.bootstrap()
    # Source order puts new listings first; historical membership should start
    # with an older listing while still including every later cohort.
    from datetime import date
    pipeline.master.loc[pipeline.master.ts_code.eq(provider.stocks.iloc[-1].ts_code), 'list_date'] = date(1990, 1, 1)
    requests = list(pipeline.history_requests("index_member_all"))
    assert requests[0][1]['ts_code'] == provider.stocks.iloc[-1].ts_code
    assert {p['ts_code'] for _, p in requests} == set(provider.stocks['ts_code'])
    assert len(requests) == len(provider.stocks) * 2
    for name, params in requests:
        pipeline.get_save(name, params)
    saved = read_dataset(pipeline.stage, "clean", "index_member_all")
    assert len(saved) == len(provider.stocks) * 2
    assert set(saved['is_new']) == {'Y', 'N'}
    assert set(saved.loc[saved.is_new.eq('N'), 'ts_code']) == set(provider.stocks.ts_code)
    recent = [p for n, p in pipeline.recent_requests('required') if n == 'index_member_all']
    assert {(p['ts_code'], p['is_new']) for p in recent} == {(p['ts_code'], p['is_new']) for _, p in requests}


def test_dividend_requests_do_not_repeat_unsupported_year_filters(tmp_path, config, client, provider, as_of):
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.master = clean.normalize("security_master", clean.security_master(provider.stocks, "2026-10-02T12:00:00Z"))
    pipeline.sessions = [as_of]
    config['download']['history_start'] = '19900101'
    requests = list(pipeline.history_requests('dividend'))
    assert len(requests) == len(provider.stocks)
    assert all(set(p) == {'ts_code'} for _, p in requests)
    recent = [p for n, p in pipeline.recent_requests('required') if n == 'dividend']
    assert recent == [p for _, p in requests]


@pytest.mark.parametrize('holiday,expected_budget', [(False, 2), (True, 5)])
def test_history_budget_follows_actual_calendar_mode(tmp_path, config, client, provider, as_of, holiday, expected_budget):
    from datetime import timedelta
    config['download'].update(history_requests_per_run=2, maintenance_history_requests_per_run=5)
    pipeline = Pipeline(tmp_path, config, client, as_of + timedelta(days=int(holiday)))
    pipeline.bootstrap()
    pipeline.history()
    state = read_json(tmp_path / 'metadata/manifest/history.json')
    assert state['mode'] == ('MAINTENANCE' if holiday else 'DAILY_UPDATE')
    assert state['request_budget'] == expected_budget
    assert state['fetched_this_run'] == expected_budget
    resumed = Pipeline(tmp_path, config, client, pipeline.as_of)
    resumed.bootstrap()
    resumed.history()
    state = read_json(tmp_path / 'metadata/manifest/history.json')
    assert state['fetched_this_run'] == expected_budget
    assert state['existing_complete_requests'] >= expected_budget


def test_history_prioritizes_recent_ranges_without_losing_oldest_requests(tmp_path, config, client, provider, as_of):
    from datetime import date
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.master = clean.normalize('security_master', clean.security_master(provider.stocks, '2026-10-02T12:00:00Z'))
    pipeline.sessions = [date(2020, 1, 2), date(2026, 9, 30)]
    config['download']['history_start'] = '20200101'
    config['index_universe'] = ['000300.SH']
    daily = list(pipeline.history_requests('daily'))
    assert [p['trade_date'] for _, p in daily] == ['20260930', '20200102']
    financial = list(pipeline.history_requests('income'))
    assert financial[0][1]['start_date'] == '20260101'
    assert financial[-1][1]['start_date'] == '20200101'
    indexes = list(pipeline.history_requests('index_daily'))
    assert indexes[0][1]['start_date'] == '20261001'
    assert indexes[-1][1]['start_date'] == '20200101'
