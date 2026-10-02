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
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.bootstrap()
    pipeline.history()
    earliest = "20260901"
    before = sum(n in ("daily", "adj_factor", "daily_basic") and p.get("trade_date") == earliest for n, p in provider.calls)
    resumed = Pipeline(tmp_path, config, client, as_of)
    resumed.capabilities, resumed.master, resumed.sessions = pipeline.capabilities, pipeline.master, pipeline.sessions
    resumed.history()
    after = sum(n in ("daily", "adj_factor", "daily_basic") and p.get("trade_date") == earliest for n, p in provider.calls)
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
