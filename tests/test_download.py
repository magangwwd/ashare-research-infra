import pandas as pd
import pytest

from data_pipeline.download import DownloadError, TushareClient, dependency_order


def test_dependency_order():
    ordered = dependency_order(["daily", "stock_basic", "trade_cal", "security_master"])
    assert ordered.index("stock_basic") < ordered.index("daily")
    assert ordered.index("trade_cal") < ordered.index("daily")
    assert ordered.index("stock_basic") < ordered.index("security_master")


def test_retries_and_token_redaction(config, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "secret-test-value")

    class Broken:
        calls = 0
        def query(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("denied secret-test-value")

    api = Broken()
    client = TushareClient(config, api, sleeper=lambda _: None)
    with pytest.raises(DownloadError) as error:
        client.fetch("daily", trade_date="20261002")
    assert api.calls == 3
    assert "secret-test-value" not in str(error.value)


def test_pagination_and_ignored_offset_fail_closed(config, provider):
    config["download"]["page_size"] = 7
    client = TushareClient(config, provider, sleeper=lambda _: None)
    assert len(client.fetch("stock_basic", list_status="L")) == 20

    class IgnoresOffset:
        def query(self, *args, **kwargs):
            return pd.DataFrame({"x": range(7)})

    client = TushareClient(config, IgnoresOffset(), sleeper=lambda _: None)
    with pytest.raises(DownloadError, match="repeated page"):
        client.fetch("daily")


def test_live_token_required(config, monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    with pytest.raises(DownloadError, match="TUSHARE_TOKEN"):
        TushareClient(config)


def test_stock_st_server_page_cap_is_respected(config):
    class Capped:
        def query(self, *args, **params):
            rows = pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(2301)]})
            offset, limit = params.get("offset", 0), min(params["limit"], 1000)
            return rows.iloc[offset:offset+limit].reset_index(drop=True)

    client = TushareClient(config, Capped(), sleeper=lambda _: None)
    assert len(client.fetch("stock_st", trade_date="20261002")) == 2301
