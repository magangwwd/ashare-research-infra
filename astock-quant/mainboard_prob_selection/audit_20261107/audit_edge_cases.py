"""Minimal deterministic reproductions. Writes only audit_20261107/fixtures."""
import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import datetime as dt

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8")


def mod(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def chronology():
    pb = mod("edge_pb", "06_protocol_backtest.py")
    days = np.array(["2026-10-27", "2026-10-30", "2026-11-02", "2026-11-03", "2026-11-06"], dtype="datetime64[D]")

    def bars(op, close, down):
        op, close = np.array(op, float), np.array(close, float)
        return (days, op, np.maximum(op, close), np.minimum(op, close), close,
                np.full(5, 100000.), np.ones(5), np.array(down, float))

    # A cannot exit Friday/Monday; Tuesday opens at its down limit and only closes sellably.
    b = {"A": bars([10, 9, 9, 9, 10], [10, 9, 9, 10, 10], [8, 9, 9, 9, 8]),
         "B": bars([20]*5, [20]*5, [15]*5)}
    def week(iid, exit_day):
        return {"exit_day": np.datetime64(exit_day), "ranked": {"v": [(iid, .8, .8, .02)]},
                "cand": [(iid, .02)], "gate_ref": {"g": .8}}
    w = {days[0]: week("A", "2026-10-30"), days[3]: week("B", "2026-11-06")}
    _, trades = pb.simulate(pb.Cfg("fixture", "v", "g", sizing="budget"), w, b, days)
    same_day_reentry = len(trades) == 2 and trades[0]["exit"] == trades[1]["entry"] == "2026-11-03"
    assert same_day_reentry, "Expected the present engine's chronology defect to reproduce"
    return {"defect_reproduced": True, "trades": trades,
            "meaning": "A exits Tuesday close, while B is funded and bought at Tuesday open on the same date."}


def forward_record():
    m = mod("edge_live", "09_weekly_signal.py")
    fixture = (OUT / "fixtures" / dt.datetime.now().strftime("%Y%m%d_%H%M%S%f") / "live").resolve()
    assert OUT.resolve() in fixture.parents
    cache, gold = fixture / "cache", fixture / "gold"
    cache.mkdir(parents=True, exist_ok=True)
    gold.mkdir(parents=True, exist_ok=True)
    p = pl.DataFrame({
        "signal_date": [dt.date(2015, 1, 5), dt.date(2026, 11, 2), dt.date(2026, 11, 2)],
        "entry_date": [dt.date(2015, 1, 6), dt.date(2026, 11, 3), dt.date(2026, 11, 3)],
        "exit_date": [dt.date(2015, 1, 9), dt.date(2026, 11, 6), dt.date(2026, 11, 6)],
        "instrument_id": ["A", "A", "B"], "buyable": [True]*3,
        "y_rel": [1, 1, 0], "y_abs": [1, 1, 0], "rk_x": [.6, .8, .7],
        "signal_close": [10.]*3, "atr_raw": [.02]*3})
    p.write_parquet(cache / "weekly_panel.parquet")
    pl.DataFrame({"instrument_id": ["A", "B"], "vendor_symbol": ["FIXTURE_A", "FIXTURE_B"],
                  "name_zh": ["fixture A", "fixture B"]}).write_parquet(gold / "instruments.parquet")
    m.LIVE, m.LIVE_CACHE = str(fixture), str(cache)
    build = SimpleNamespace(CACHE=str(cache), GOLD=str(gold))
    train = SimpleNamespace(LGB_PARAMS={}, SEED=1)
    m.mod = lambda name, _: build if name == "build" else train

    class FakeModel:
        reverse = False
        def __init__(self, **kwargs): pass
        def fit(self, x, y): return self
        def predict_proba(self, x):
            p = 1-x[:, 0] if self.reverse else x[:, 0]
            return np.column_stack([1-p, p])

    real_model = m.lgb.LGBMClassifier
    argv = sys.argv[:]
    try:
        m.lgb.LGBMClassifier = FakeModel
        log = fixture / "forward_log.csv"
        sys.argv = ["09_weekly_signal.py", "--skip-build"]
        with contextlib.redirect_stdout(io.StringIO()):
            m.main()
        first_log = log.read_bytes()
        signal = fixture / "signal_2026-11-02.csv"
        first_signal = signal.read_bytes()
        FakeModel.reverse = True
        with contextlib.redirect_stdout(io.StringIO()) as second_output:
            m.main()
        result = {"log_unchanged": log.read_bytes() == first_log,
                  "signal_overwritten": signal.read_bytes() != first_signal,
                  "first_logged_pick": pl.read_csv(log)["pick"][0],
                  "second_signal_pick": pl.read_csv(signal)["vendor_symbol"][0],
                  "past_entry_accepted": True, "entry_date": "2026-11-03",
                  "message": second_output.getvalue().splitlines()[0]}
        assert result["log_unchanged"] and result["signal_overwritten"]
        return result
    finally:
        m.lgb.LGBMClassifier = real_model
        sys.argv = argv


result = {"chronology": chronology(), "forward_record": forward_record()}
(OUT / "edge_case_evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
