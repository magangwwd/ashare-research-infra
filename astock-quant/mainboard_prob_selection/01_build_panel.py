"""Step 1: build the weekly cross-sectional panel for the full main board.

Reads the gold daily tables once, computes daily features per stock, samples them
at each week's signal date (the trading day before Tuesday's open), attaches
Tuesday-open -> week-end labels and tradability flags, and writes:

  cache/weekly_panel.parquet   one row per (week, stock) in the eligible universe
  cache/daily_sim.parquet      raw OHLC + limits + adj factor for the simulation
"""
from __future__ import annotations

import glob
import os
import time

import numpy as np
import polars as pl

GOLD = os.environ.get("PROB_GOLD_ROOT", r"G:\我的云端硬盘\project\down_data\data\gold\main_board")
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
os.makedirs(CACHE, exist_ok=True)

LOAD_FROM_YEAR = 2013          # history buffer for 120-day features before 2015
SIM_FROM = pl.date(2019, 12, 1)
MIN_AGE_DAYS = 120             # trading days since first bar
MIN_AMT20 = 2e7                # 20-day mean turnover >= 20m CNY
MIN_PRICE = 2.0


def month_files(table: str) -> list[str]:
    out = []
    for f in glob.glob(os.path.join(GOLD, table, "year=*", "month=*", "data.parquet")):
        year = int(f.split("year=")[1][:4])
        if year >= LOAD_FROM_YEAR:
            out.append(f)
    return sorted(out)


def load(table: str, cols: list[str]) -> pl.DataFrame:
    frames = [pl.read_parquet(f, columns=cols) for f in month_files(table)]
    return pl.concat(frames, how="vertical_relaxed")


def main() -> None:
    t0 = time.time()
    px = load("daily_prices", ["instrument_id", "trade_date", "open", "high", "low", "close",
                               "previous_close", "volume_shares", "turnover_cny"])
    mt = load("daily_metrics", ["instrument_id", "trade_date", "turnover_rate_pct",
                                "free_float_turnover_rate_pct", "volume_ratio",
                                "price_earnings_ttm", "price_book", "dividend_yield_ttm_pct",
                                "float_market_cap_cny"])
    af = load("adjustment_factors", ["instrument_id", "trade_date", "adjustment_factor"])
    lim = load("daily_limits", ["instrument_id", "trade_date", "up_limit", "down_limit"])
    print(f"loaded px={px.height:,} mt={mt.height:,} af={af.height:,} lim={lim.height:,} "
          f"in {time.time()-t0:.0f}s")

    df = (px.join(mt, on=["instrument_id", "trade_date"], how="left")
            .join(af, on=["instrument_id", "trade_date"], how="left")
            .join(lim, on=["instrument_id", "trade_date"], how="left")
            .sort(["instrument_id", "trade_date"]))
    df = df.with_columns(pl.col("adjustment_factor").forward_fill().over("instrument_id"))
    df = df.filter(pl.col("adjustment_factor").is_not_null() & (pl.col("close") > 0))

    # --- ST flag from historical name intervals -------------------------------------
    nh = pl.read_parquet(os.path.join(GOLD, "name_history.parquet"),
                         columns=["instrument_id", "effective_date", "end_date", "is_st"])
    st = nh.filter(pl.col("is_st")).select(
        "instrument_id", pl.col("effective_date").alias("st_from"),
        pl.col("end_date").fill_null(pl.date(2099, 1, 1)).alias("st_to"))
    st_days = (df.select("instrument_id", "trade_date")
                 .join(st, on="instrument_id", how="inner")
                 .filter((pl.col("trade_date") >= pl.col("st_from")) &
                         (pl.col("trade_date") <= pl.col("st_to")))
                 .select("instrument_id", "trade_date").unique()
                 .with_columns(pl.lit(True).alias("is_st")))
    df = df.join(st_days, on=["instrument_id", "trade_date"], how="left").with_columns(
        pl.col("is_st").fill_null(False))

    # --- daily features (backward-adjusted prices) -------------------------------------
    f = pl.col("adjustment_factor")
    df = df.with_columns(
        (pl.col("open") * f).alias("o_h"), (pl.col("high") * f).alias("h_h"),
        (pl.col("low") * f).alias("l_h"), (pl.col("close") * f).alias("c_h"),
    )
    g = "instrument_id"
    c = pl.col("c_h")
    df = df.with_columns(
        (c / c.shift(1).over(g) - 1).alias("r1"),
        pl.int_range(pl.len()).over(g).alias("age"),
        (pl.col("close") / pl.col("previous_close") - 1).alias("r_raw"),
    )
    lim_pct = pl.when(pl.col("is_st")).then(0.05).otherwise(0.10)
    r = pl.col("r1")
    tr = (pl.max_horizontal(pl.col("h_h"), c.shift(1).over(g)) -
          pl.min_horizontal(pl.col("l_h"), c.shift(1).over(g))) / c.shift(1).over(g)
    df = df.with_columns(
        (pl.col("r_raw") >= lim_pct - 0.005).cast(pl.Int8).alias("lu"),
        (pl.col("r_raw") <= -lim_pct + 0.005).cast(pl.Int8).alias("ld"),
        tr.alias("tr"),
        (pl.col("close") / pl.col("open") - 1).alias("intraday"),
        (pl.col("open") / pl.col("previous_close") - 1).alias("gap"),
    )
    feats = [
        *[(c / c.shift(k).over(g) - 1).alias(f"ret_{k}") for k in (5, 10, 20, 60, 120)],
        (c.shift(20).over(g) / c.shift(120).over(g) - 1).alias("mom_120_20"),
        r.rolling_std(20).over(g).alias("vol_20"),
        r.rolling_std(60).over(g).alias("vol_60"),
        r.rolling_max(20).over(g).alias("max_r_20"),
        r.rolling_min(20).over(g).alias("min_r_20"),
        r.rolling_skew(20).over(g).alias("skew_20"),
        pl.col("tr").rolling_mean(14).over(g).alias("atr_14"),
        (c / pl.col("h_h").rolling_max(20).over(g) - 1).alias("to_high_20"),
        (c / pl.col("l_h").rolling_min(20).over(g) - 1).alias("to_low_20"),
        (c / pl.col("h_h").rolling_max(250, min_samples=120).over(g) - 1).alias("to_high_250"),
        (c / c.rolling_mean(20).over(g) - 1).alias("ma_gap_20"),
        (c / c.rolling_mean(60).over(g) - 1).alias("ma_gap_60"),
        pl.col("turnover_rate_pct").rolling_mean(5).over(g).alias("turn_5"),
        pl.col("turnover_rate_pct").rolling_mean(20).over(g).alias("turn_20"),
        pl.col("turnover_cny").cast(pl.Float64).rolling_mean(20).over(g).alias("amt_20"),
        pl.col("lu").cast(pl.Float64).rolling_sum(20).over(g).alias("lu_20"),
        pl.col("ld").cast(pl.Float64).rolling_sum(20).over(g).alias("ld_20"),
        pl.col("intraday").rolling_mean(5).over(g).alias("intraday_5"),
        pl.col("gap").rolling_mean(5).over(g).alias("gap_5"),
        pl.col("volume_shares").cast(pl.Float64).rolling_mean(5).over(g).alias("vol5"),
        pl.col("volume_shares").cast(pl.Float64).rolling_mean(60).over(g).alias("vol60"),
    ]
    df = df.with_columns(feats)
    df = df.with_columns(
        (pl.col("turn_5") / pl.col("turn_20")).alias("turn_ratio"),
        (pl.col("vol5") / pl.col("vol60")).alias("volu_ratio_5_60"),
        pl.col("amt_20").log().alias("log_amt_20"),
        pl.col("float_market_cap_cny").cast(pl.Float64).log().alias("log_fmcap"),
        (1 / pl.col("price_earnings_ttm")).fill_null(0.0).alias("ep"),
        (1 / pl.col("price_book")).alias("bp"),
        pl.col("dividend_yield_ttm_pct").fill_null(0.0).alias("dy"),
        pl.col("age").cast(pl.Float64).log1p().alias("log_age"),
    )
    print(f"features done in {time.time()-t0:.0f}s, rows={df.height:,}")

    # --- simulation table ---------------------------------------------------------------
    sim = df.filter(pl.col("trade_date") >= SIM_FROM).select(
        "instrument_id", "trade_date", "open", "high", "low", "close", "previous_close",
        "volume_shares", "adjustment_factor", "up_limit", "down_limit", "is_st")
    sim.write_parquet(os.path.join(CACHE, "daily_sim.parquet"))

    # --- weekly schedule: signal = trading day before Tuesday, exit = last day of week ---
    import sys as _sys
    _sys.path.insert(0, HERE)
    import core as _core  # full calendar incl. future days (needed for the newest week)
    cal = (pl.DataFrame({"d": _core.trading_days()}).sort("d")
             .filter(pl.col("d").dt.year() >= LOAD_FROM_YEAR))
    max_data_day = df["trade_date"].max()
    cal = cal.with_columns(pl.col("d").shift(1).alias("prev_d"),
                           pl.col("d").dt.strftime("%G-%V").alias("wk"))
    week_end = cal.group_by("wk").agg(pl.col("d").max().alias("exit_date"))
    sched = (cal.filter(pl.col("d").dt.weekday() == 2)      # Tuesday is a trading day
                .rename({"d": "entry_date", "prev_d": "signal_date"})
                .join(week_end, on="wk")
                .filter(pl.col("exit_date") > pl.col("entry_date"))
                .filter(pl.col("signal_date") <= max_data_day)
                .select("wk", "signal_date", "entry_date", "exit_date").sort("signal_date"))
    print(f"weeks in schedule: {sched.height}")

    feat_cols = ["ret_5", "ret_10", "ret_20", "ret_60", "ret_120", "mom_120_20", "r1",
                 "vol_20", "vol_60", "max_r_20", "min_r_20", "skew_20", "atr_14",
                 "to_high_20", "to_low_20", "to_high_250", "ma_gap_20", "ma_gap_60",
                 "turn_5", "turn_20", "turn_ratio", "volu_ratio_5_60", "volume_ratio",
                 "log_amt_20", "log_fmcap", "ep", "bp", "dy", "lu_20", "ld_20",
                 "intraday_5", "gap_5", "log_age"]

    snap = df.join(sched.select("signal_date").rename({"signal_date": "trade_date"}),
                   on="trade_date", how="semi")
    snap = snap.filter(
        (pl.col("volume_shares") > 0) & (~pl.col("is_st")) &
        (pl.col("age") >= MIN_AGE_DAYS) & (pl.col("amt_20") >= MIN_AMT20) &
        (pl.col("close") >= MIN_PRICE) & pl.col("ret_60").is_not_null())
    snap = snap.select(["instrument_id", pl.col("trade_date").alias("signal_date"),
                        pl.col("close").alias("signal_close"), pl.col("atr_14").alias("atr_raw"),
                        pl.col("amt_20").alias("amt20_raw"), *feat_cols])

    # entry (Tuesday) and exit (week end) bars
    bars = df.select("instrument_id", "trade_date", "open", "close", "o_h", "c_h",
                     "volume_shares", "up_limit", "down_limit", "previous_close", "is_st")
    ent = bars.rename({c_: f"e_{c_}" for c_ in bars.columns if c_ != "instrument_id"})
    ext = bars.rename({c_: f"x_{c_}" for c_ in bars.columns if c_ != "instrument_id"})
    panel = (snap.join(sched, on="signal_date")
                 .join(ent, left_on=["instrument_id", "entry_date"],
                       right_on=["instrument_id", "e_trade_date"], how="left")
                 .join(ext, left_on=["instrument_id", "exit_date"],
                       right_on=["instrument_id", "x_trade_date"], how="left"))
    up_lim = pl.coalesce(pl.col("e_up_limit"),
                         (pl.col("e_previous_close") * 1.10).round(2))
    panel = panel.with_columns(
        ((pl.col("e_volume_shares") > 0) & (pl.col("e_open") < up_lim - 0.001))
        .fill_null(False).alias("buyable"),
        (pl.col("x_c_h") / pl.col("e_o_h") - 1).alias("ret_close"),
        (pl.col("x_o_h") / pl.col("e_o_h") - 1).alias("ret_open"),
    ).drop([c_ for c_ in panel.columns if c_.startswith(("e_", "x_"))
            and c_ not in ("e_open",)])

    # labels: relative (beat cross-sectional median of buyable stocks) and absolute
    lab = panel.filter(pl.col("buyable") & pl.col("ret_close").is_not_null())
    med = lab.group_by("signal_date").agg(pl.col("ret_close").median().alias("med_close"),
                                          pl.col("ret_open").median().alias("med_open"))
    panel = panel.join(med, on="signal_date", how="left").with_columns(
        pl.when(pl.col("ret_close").is_null()).then(None)
          .otherwise((pl.col("ret_close") > pl.col("med_close")).cast(pl.Int8)).alias("y_rel"),
        pl.when(pl.col("ret_close").is_null()).then(None)
          .otherwise((pl.col("ret_close") > 0).cast(pl.Int8)).alias("y_abs"),
    )

    # cross-sectional percentile ranks for stock features + market-state features
    rank_exprs = [((pl.col(c_).rank("average").over("signal_date") - 1) /
                   (pl.col(c_).count().over("signal_date") - 1)).alias(f"rk_{c_}")
                  for c_ in feat_cols]
    mkt_exprs = [pl.col("ret_5").median().over("signal_date").alias("mkt_ret_5"),
                 pl.col("ret_20").median().over("signal_date").alias("mkt_ret_20"),
                 pl.col("ret_60").median().over("signal_date").alias("mkt_ret_60"),
                 (pl.col("ret_20") > 0).mean().over("signal_date").alias("mkt_breadth_20"),
                 pl.col("vol_20").median().over("signal_date").alias("mkt_vol_20"),
                 pl.col("lu_20").mean().over("signal_date").alias("mkt_lu_20"),
                 pl.col("turn_ratio").median().over("signal_date").alias("mkt_turn_ratio")]
    panel = panel.with_columns(rank_exprs + mkt_exprs)
    panel = panel.drop(feat_cols).sort(["signal_date", "instrument_id"])
    panel.write_parquet(os.path.join(CACHE, "weekly_panel.parquet"))

    per_week = panel.group_by("signal_date").len()
    print(f"panel rows={panel.height:,} weeks={per_week.height} "
          f"stocks/week median={int(per_week['len'].median())} "
          f"min={per_week['len'].min()} max={per_week['len'].max()} "
          f"last signal={panel['signal_date'].max()}  total {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
