from __future__ import annotations

import argparse
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import tushare as ts


PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
CACHE_DIR = PACKAGE_DIR / "cache"
OUTPUT_DIR = PACKAGE_DIR / "output"
FINANCIAL_CACHE = CACHE_DIR / "financial_history.csv"
COLLECTION_STATE = CACHE_DIR / "collection_state.json"
VALUATION_CACHE = CACHE_DIR / "valuation_signals.csv"
MAINBOARD_CSV = REPO_ROOT / "filtered_stocks.csv"
INDUSTRY_CSV = REPO_ROOT / "industry_map.csv"
DAILY_DATA_DIR = REPO_ROOT / "daily_data"

FINANCE_INDUSTRIES = {"银行", "保险", "证券", "多元金融", "信托", "租赁"}


@dataclass(frozen=True)
class BacktestConfig:
    start_year: int = 2020
    end_year: int = 2025
    lookback_years: int = 5
    min_years: int = 4
    top_k: int = 20
    cost_bps_each_side: float = 20.0
    max_debt_to_assets: float = 70.0


_thread_local = threading.local()
_rate_lock = threading.Lock()
_next_api_call = 0.0


def _pro():
    client = getattr(_thread_local, "pro", None)
    if client is None:
        client = ts.pro_api()
        _thread_local.pro = client
    return client


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", **kwargs)


def _normalize_date(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.replace("-", "", regex=False)
        .str.slice(0, 8)
    )


def _fetch_with_retry(endpoint: str, ts_code: str, fields: str) -> pd.DataFrame:
    global _next_api_call
    delay = 1.0
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            with _rate_lock:
                now = time.monotonic()
                wait = max(0.0, _next_api_call - now)
                if wait:
                    time.sleep(wait)
                _next_api_call = time.monotonic() + 0.32
            return getattr(_pro(), endpoint)(
                ts_code=ts_code,
                start_date="20140101",
                end_date="20251231",
                fields=fields,
            )
        except Exception as exc:
            last_error = exc
            if attempt == 4:
                break
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"{endpoint} failed for {ts_code}: {last_error}")


def _collect_one(ts_code: str) -> pd.DataFrame:
    fina = _fetch_with_retry(
        "fina_indicator",
        ts_code,
        "ts_code,ann_date,end_date,roe,debt_to_assets",
    )
    cash = _fetch_with_retry(
        "cashflow",
        ts_code,
        "ts_code,ann_date,end_date,n_cashflow_act,c_pay_acq_const_fiolta",
    )

    frames: list[pd.DataFrame] = []
    if not fina.empty:
        fina = fina.copy()
        fina["source"] = "fina"
        frames.append(fina)
    if not cash.empty:
        cash = cash.copy()
        cash["source"] = "cash"
        frames.append(cash)
    if not frames:
        return pd.DataFrame(
            columns=[
                "ts_code",
                "ann_date",
                "end_date",
                "roe",
                "debt_to_assets",
                "n_cashflow_act",
                "c_pay_acq_const_fiolta",
                "source",
            ]
        )

    result = pd.concat(frames, ignore_index=True, sort=False)
    result["ann_date"] = _normalize_date(result["ann_date"])
    result["end_date"] = _normalize_date(result["end_date"])
    result = result[result["end_date"].str.endswith("1231", na=False)]
    return result


def collect_financial_history(
    codes: list[str],
    workers: int,
    force: bool,
    quiet: bool,
) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if force:
        FINANCIAL_CACHE.unlink(missing_ok=True)
        COLLECTION_STATE.unlink(missing_ok=True)

    if FINANCIAL_CACHE.exists():
        cached = _read_csv(
            FINANCIAL_CACHE,
            dtype={"ts_code": str, "ann_date": str, "end_date": str},
        )
    else:
        cached = pd.DataFrame()

    if COLLECTION_STATE.exists():
        state = json.loads(COLLECTION_STATE.read_text(encoding="utf-8"))
        completed = set(state.get("completed_codes", []))
    else:
        completed = set()

    todo = [code for code in codes if code not in completed]
    if not quiet:
        print(
            f"[financial] universe={len(codes)}, completed={len(completed)}, "
            f"todo={len(todo)}",
            flush=True,
        )
    if not todo:
        return cached

    collected: list[pd.DataFrame] = [cached] if not cached.empty else []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_collect_one, code): code for code in todo}
        for index, future in enumerate(as_completed(futures), start=1):
            code = futures[future]
            try:
                frame = future.result()
                if not frame.empty:
                    collected.append(frame)
                completed.add(code)
            except Exception as exc:
                failures.append(code)
                print(f"[financial] failed {code}: {exc}", flush=True)

            if index % 50 == 0 or index == len(todo):
                current = (
                    pd.concat(collected, ignore_index=True, sort=False)
                    if collected
                    else pd.DataFrame()
                )
                if not current.empty:
                    current = current.drop_duplicates(
                        subset=["ts_code", "source", "ann_date", "end_date"],
                        keep="last",
                    )
                    _write_csv(current, FINANCIAL_CACHE)
                    collected = [current]
                COLLECTION_STATE.write_text(
                    json.dumps(
                        {
                            "completed_codes": sorted(completed),
                            "failed_codes": sorted(set(failures)),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                if not quiet:
                    print(
                        f"[financial] progress={index}/{len(todo)}, "
                        f"failed={len(failures)}",
                        flush=True,
                    )

            # The standard financial endpoints are rate limited.
            time.sleep(0.15)

    if failures:
        print(
            f"[financial] warning: {len(failures)} codes failed and were excluded",
            flush=True,
        )
    return _read_csv(
        FINANCIAL_CACHE,
        dtype={"ts_code": str, "ann_date": str, "end_date": str},
    )


def load_prices(codes: list[str], workers: int, quiet: bool) -> dict[str, pd.DataFrame]:
    def load_one(code: str) -> tuple[str, pd.DataFrame | None]:
        path = DAILY_DATA_DIR / f"{code}.csv"
        if not path.exists():
            return code, None
        try:
            frame = pd.read_csv(
                path,
                usecols=["Date", "Close", "Open", "Volume"],
                parse_dates=["Date"],
            )
            frame = frame.dropna(subset=["Date", "Close", "Open"])
            frame = frame.drop_duplicates("Date", keep="last").sort_values("Date")
            frame = frame.set_index("Date")
            return code, frame
        except Exception:
            return code, None

    result: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(load_one, code) for code in codes]
        for index, future in enumerate(as_completed(futures), start=1):
            code, frame = future.result()
            if frame is not None and not frame.empty:
                result[code] = frame
            if not quiet and index % 500 == 0:
                print(f"[prices] loaded={index}/{len(codes)}", flush=True)
    return result


def trading_dates_from_prices(prices: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    for preferred in ("000001.SZ", "600000.SH"):
        if preferred in prices:
            return pd.DatetimeIndex(prices[preferred].index).sort_values().unique()
    dates = pd.DatetimeIndex([])
    for frame in prices.values():
        dates = dates.union(frame.index)
    return dates.sort_values().unique()


def first_trading_date_on_or_after(
    trading_dates: pd.DatetimeIndex, date: pd.Timestamp
) -> pd.Timestamp:
    position = int(trading_dates.searchsorted(date, side="left"))
    if position >= len(trading_dates):
        raise ValueError(f"No trading date on or after {date.date()}")
    return pd.Timestamp(trading_dates[position])


def next_trading_date(
    trading_dates: pd.DatetimeIndex, date: pd.Timestamp
) -> pd.Timestamp:
    position = int(trading_dates.searchsorted(date, side="right"))
    if position >= len(trading_dates):
        raise ValueError(f"No trading date after {date.date()}")
    return pd.Timestamp(trading_dates[position])


def build_schedule(
    trading_dates: pd.DatetimeIndex, config: BacktestConfig
) -> pd.DataFrame:
    rows = []
    for year in range(config.start_year, config.end_year + 2):
        signal = first_trading_date_on_or_after(
            trading_dates, pd.Timestamp(year=year, month=5, day=1)
        )
        entry = next_trading_date(trading_dates, signal)
        rows.append({"year": year, "signal_date": signal, "entry_date": entry})
    schedule = pd.DataFrame(rows)
    schedule["exit_date"] = schedule["entry_date"].shift(-1)
    return schedule.iloc[:-1].copy()


def collect_valuations(schedule: pd.DataFrame, force: bool = False) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if VALUATION_CACHE.exists() and not force:
        cached = _read_csv(
            VALUATION_CACHE,
            dtype={"ts_code": str, "trade_date": str},
        )
    else:
        cached = pd.DataFrame()

    existing_dates = (
        set(cached["trade_date"].astype(str))
        if not cached.empty and "trade_date" in cached
        else set()
    )
    frames = [cached] if not cached.empty else []
    for signal in schedule["signal_date"]:
        trade_date = pd.Timestamp(signal).strftime("%Y%m%d")
        if trade_date in existing_dates:
            continue
        frame = _pro().daily_basic(
            trade_date=trade_date,
            fields="ts_code,trade_date,pe_ttm,pb,total_mv",
        )
        if not frame.empty:
            frames.append(frame)
    result = pd.concat(frames, ignore_index=True, sort=False)
    result["trade_date"] = _normalize_date(result["trade_date"])
    result = result.drop_duplicates(["ts_code", "trade_date"], keep="last")
    _write_csv(result, VALUATION_CACHE)
    return result


def latest_asof(
    source: pd.DataFrame,
    signal_date: pd.Timestamp,
    source_name: str,
) -> pd.DataFrame:
    signal = signal_date.strftime("%Y%m%d")
    frame = source[
        (source["source"] == source_name)
        & (source["ann_date"] <= signal)
        & (source["end_date"] < signal)
    ].copy()
    if frame.empty:
        return frame
    frame = frame.sort_values(["ts_code", "end_date", "ann_date"])
    return frame.drop_duplicates(["ts_code", "end_date"], keep="last")


def _normalized_slope(values: pd.Series) -> float:
    values = values.dropna().astype(float)
    if len(values) < 3:
        return np.nan
    denominator = float(values.abs().median())
    if denominator <= 0:
        return np.nan
    return float(np.polyfit(np.arange(len(values)), values.to_numpy(), 1)[0] / denominator)


def build_metrics_for_signal(
    history: pd.DataFrame,
    signal_date: pd.Timestamp,
    config: BacktestConfig,
    metadata: pd.DataFrame,
    valuation: pd.DataFrame,
) -> pd.DataFrame:
    prior_year = signal_date.year - 1
    start_period = f"{prior_year - config.lookback_years + 1}1231"
    end_period = f"{prior_year}1231"

    fina = latest_asof(history, signal_date, "fina")
    cash = latest_asof(history, signal_date, "cash")
    fina = fina[fina["end_date"].between(start_period, end_period)]
    cash = cash[cash["end_date"].between(start_period, end_period)]

    for column in ("roe", "debt_to_assets"):
        fina[column] = pd.to_numeric(fina[column], errors="coerce")
    for column in ("n_cashflow_act", "c_pay_acq_const_fiolta"):
        cash[column] = pd.to_numeric(cash[column], errors="coerce")
    cash["fcf"] = cash["n_cashflow_act"] - cash["c_pay_acq_const_fiolta"]

    rows: list[dict] = []
    fina_groups = {code: group for code, group in fina.groupby("ts_code")}
    cash_groups = {code: group for code, group in cash.groupby("ts_code")}
    for code in set(fina_groups) | set(cash_groups):
        fg = fina_groups.get(code)
        cg = cash_groups.get(code)
        if fg is None:
            fg = pd.DataFrame(columns=["end_date", "roe", "debt_to_assets"])
        else:
            fg = fg.sort_values("end_date")
        if cg is None:
            cg = pd.DataFrame(columns=["end_date", "fcf"])
        else:
            cg = cg.sort_values("end_date")
        roe = fg["roe"].dropna() if "roe" in fg else pd.Series(dtype=float)
        fcf = cg["fcf"].dropna() if "fcf" in cg else pd.Series(dtype=float)
        debt = (
            float(fg.iloc[-1]["debt_to_assets"])
            if not fg.empty and pd.notna(fg.iloc[-1]["debt_to_assets"])
            else np.nan
        )
        rows.append(
            {
                "ts_code": code,
                "signal_date": signal_date,
                "roe_count": len(roe),
                "roe_median": float(roe.median()) if len(roe) else np.nan,
                "roe_std": float(roe.std(ddof=0)) if len(roe) else np.nan,
                "roe_positive_years": int((roe > 0).sum()),
                "roe_above_15_years": int((roe >= 15).sum()),
                "fcf_count": len(fcf),
                "fcf_positive_years": int((fcf > 0).sum()),
                "fcf_positive_ratio": float((fcf > 0).mean()) if len(fcf) else np.nan,
                "fcf_sum": float(fcf.sum()) if len(fcf) else np.nan,
                "fcf_trend": _normalized_slope(fcf),
                "debt_to_assets": debt,
            }
        )
    metrics = pd.DataFrame(rows)
    metrics = metrics.merge(metadata, on="ts_code", how="left")

    signal_key = signal_date.strftime("%Y%m%d")
    val = valuation[valuation["trade_date"] == signal_key].copy()
    metrics = metrics.merge(
        val[["ts_code", "pe_ttm", "pb", "total_mv"]],
        on="ts_code",
        how="left",
    )

    metrics["roe_eligible"] = (
        (metrics["roe_count"] >= config.min_years)
        & (metrics["roe_median"] >= 15)
        & (metrics["roe_positive_years"] >= config.min_years)
    )
    metrics["fcf_eligible"] = (
        (metrics["fcf_count"] >= config.min_years)
        & (metrics["fcf_positive_years"] >= config.min_years)
        & (metrics["fcf_sum"] > 0)
    )
    metrics["strict_quality"] = (
        metrics["roe_eligible"]
        & metrics["fcf_eligible"]
        & (metrics["debt_to_assets"] <= config.max_debt_to_assets)
        & (~metrics["industry"].isin(FINANCE_INDUSTRIES))
    )

    metrics["roe_rank"] = metrics["roe_median"].rank(pct=True)
    metrics["stability_rank"] = (-metrics["roe_std"]).rank(pct=True)
    metrics["fcf_positive_rank"] = metrics["fcf_positive_ratio"].rank(pct=True)
    metrics["fcf_trend_rank"] = metrics["fcf_trend"].rank(pct=True)
    metrics["debt_rank"] = (-metrics["debt_to_assets"]).rank(pct=True)
    metrics["quality_score"] = (
        0.40 * metrics["roe_rank"]
        + 0.20 * metrics["stability_rank"]
        + 0.20 * metrics["fcf_positive_rank"]
        + 0.10 * metrics["fcf_trend_rank"].fillna(0.5)
        + 0.10 * metrics["debt_rank"]
    )
    positive_pe = pd.to_numeric(metrics["pe_ttm"], errors="coerce").where(
        pd.to_numeric(metrics["pe_ttm"], errors="coerce") > 0
    )
    metrics["earnings_yield"] = 1.0 / positive_pe
    metrics["value_rank"] = metrics["earnings_yield"].rank(pct=True)
    metrics["quality_value_score"] = (
        0.70 * metrics["quality_score"] + 0.30 * metrics["value_rank"]
    )
    return metrics


def executable_return(
    price: pd.DataFrame,
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
    cost_bps_each_side: float,
) -> dict | None:
    if entry_date not in price.index or exit_date not in price.index:
        return None
    entry = price.loc[entry_date]
    exit_row = price.loc[exit_date]
    if (
        not np.isfinite(entry["Open"])
        or not np.isfinite(exit_row["Open"])
        or entry["Open"] <= 0
        or exit_row["Open"] <= 0
        or entry.get("Volume", 0) <= 0
    ):
        return None
    previous = price.loc[price.index < entry_date]
    if not previous.empty:
        previous_close = float(previous.iloc[-1]["Close"])
        if previous_close > 0 and float(entry["Open"]) / previous_close - 1.0 >= 0.095:
            return None

    cost = cost_bps_each_side / 10_000.0
    entry_price = float(entry["Open"]) * (1.0 + cost)
    exit_price = float(exit_row["Open"]) * (1.0 - cost)
    return {
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_return": float(exit_row["Open"]) / float(entry["Open"]) - 1.0,
        "net_return": exit_price / entry_price - 1.0,
    }


def select_strategies(
    metrics: pd.DataFrame,
    price_eligible_codes: set[str],
    top_k: int,
) -> dict[str, list[str]]:
    base = metrics[
        metrics["ts_code"].isin(price_eligible_codes)
        & (~metrics["industry"].isin(FINANCE_INDUSTRIES))
    ].copy()
    benchmark_codes = sorted(base["ts_code"].dropna().unique())
    strict = base[base["strict_quality"]].copy()
    roe = base[base["roe_eligible"]].nlargest(top_k, "quality_score")
    fcf = base[base["fcf_eligible"]].nlargest(top_k, "fcf_trend_rank")
    quality = strict.nlargest(top_k, "quality_score")
    value = strict.dropna(subset=["quality_value_score"]).nlargest(
        top_k, "quality_value_score"
    )
    return {
        "benchmark_mainboard": benchmark_codes,
        f"roe_only_top{top_k}": roe["ts_code"].tolist(),
        f"fcf_only_top{top_k}": fcf["ts_code"].tolist(),
        "roe_fcf_all": strict["ts_code"].tolist(),
        f"roe_fcf_top{top_k}": quality["ts_code"].tolist(),
        f"roe_fcf_value_top{top_k}": value["ts_code"].tolist(),
    }


def period_path(
    codes: list[str],
    prices: dict[str, pd.DataFrame],
    trading_dates: pd.DatetimeIndex,
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
    cost_bps_each_side: float,
) -> tuple[pd.Series, pd.DataFrame]:
    dates = trading_dates[
        (trading_dates >= entry_date) & (trading_dates <= exit_date)
    ]
    paths: list[pd.Series] = []
    trade_rows: list[dict] = []
    cost = cost_bps_each_side / 10_000.0
    for code in codes:
        price = prices.get(code)
        if price is None:
            continue
        trade = executable_return(
            price, entry_date, exit_date, cost_bps_each_side
        )
        if trade is None:
            continue
        close = price["Close"].reindex(dates).ffill()
        normalized = close / trade["entry_price"]
        normalized.loc[exit_date] = trade["exit_price"] / trade["entry_price"]
        if normalized.notna().sum() < 2:
            continue
        paths.append(normalized.rename(code))
        trade_rows.append(
            {
                "ts_code": code,
                "entry_date": entry_date,
                "exit_date": exit_date,
                **trade,
            }
        )
    if not paths:
        return pd.Series(dtype=float), pd.DataFrame()
    panel = pd.concat(paths, axis=1)
    panel = panel.ffill()
    portfolio = panel.mean(axis=1, skipna=True)
    return portfolio, pd.DataFrame(trade_rows)


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return np.nan
    return float((equity / equity.cummax() - 1.0).min())


def summarize_equity(
    strategy: str,
    equity: pd.Series,
    periods: pd.DataFrame,
) -> dict:
    daily_returns = equity.pct_change().dropna()
    days = max((equity.index.max() - equity.index.min()).days, 1)
    years = days / 365.25
    compound = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    annualized = (
        float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0)
        if years > 0 and equity.iloc[0] > 0
        else np.nan
    )
    volatility = (
        float(daily_returns.std(ddof=0) * math.sqrt(252))
        if len(daily_returns)
        else np.nan
    )
    sharpe = (
        float(daily_returns.mean() / daily_returns.std(ddof=0) * math.sqrt(252))
        if len(daily_returns) and daily_returns.std(ddof=0) > 0
        else np.nan
    )
    drawdown = max_drawdown(equity)
    return {
        "strategy": strategy,
        "periods": len(periods),
        "avg_holdings": float(periods["executed_count"].mean()),
        "compound_return": compound,
        "annualized_return": annualized,
        "annualized_volatility": volatility,
        "max_drawdown": drawdown,
        "sharpe": sharpe,
        "calmar": annualized / abs(drawdown) if drawdown < 0 else np.nan,
        "period_win_rate": float((periods["net_return"] > 0).mean()),
        "avg_period_return": float(periods["net_return"].mean()),
    }


def factor_diagnostics(
    metrics: pd.DataFrame,
    trades: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    benchmark = trades[
        trades["strategy"] == "benchmark_mainboard"
    ][["signal_date", "ts_code", "net_return"]]
    joined = metrics.merge(
        benchmark, on=["signal_date", "ts_code"], how="inner"
    )
    joined["roe_stability"] = -joined["roe_std"]

    features = [
        "roe_median",
        "roe_stability",
        "fcf_positive_ratio",
        "fcf_trend",
        "quality_score",
        "earnings_yield",
        "quality_value_score",
    ]
    ic_rows: list[dict] = []
    quintile_rows: list[dict] = []
    for signal_date, group in joined.groupby("signal_date"):
        for feature in features:
            clean = (
                group[[feature, "net_return"]]
                .replace([np.inf, -np.inf], np.nan)
                .dropna()
            )
            ic = (
                clean[feature].corr(clean["net_return"], method="spearman")
                if len(clean) >= 20
                else np.nan
            )
            ic_rows.append(
                {
                    "signal_date": signal_date,
                    "feature": feature,
                    "n": len(clean),
                    "spearman_ic": ic,
                }
            )

        complete = group[
            (group["roe_count"] >= 4)
            & (group["fcf_count"] >= 4)
            & group["quality_score"].notna()
        ].copy()
        if len(complete) >= 100:
            complete["quality_quintile"] = (
                pd.qcut(
                    complete["quality_score"].rank(method="first"),
                    5,
                    labels=False,
                )
                + 1
            )
            for quintile, subset in complete.groupby("quality_quintile"):
                quintile_rows.append(
                    {
                        "signal_date": signal_date,
                        "quality_quintile": int(quintile),
                        "n": len(subset),
                        "net_return": float(subset["net_return"].mean()),
                    }
                )
    return pd.DataFrame(ic_rows), pd.DataFrame(quintile_rows)


def run_backtest(
    config: BacktestConfig,
    metadata: pd.DataFrame,
    history: pd.DataFrame,
    valuation: pd.DataFrame,
    prices: dict[str, pd.DataFrame],
    output_prefix: str,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    trading_dates = trading_dates_from_prices(prices)
    schedule = build_schedule(trading_dates, config)
    all_metrics: list[pd.DataFrame] = []
    period_rows: list[dict] = []
    trade_frames: list[pd.DataFrame] = []
    equity_parts: dict[str, list[pd.Series]] = {}

    metadata = metadata.copy()
    metadata["list_date"] = _normalize_date(metadata["list_date"])
    metadata["industry"] = metadata["industry"].fillna("")

    for period in schedule.itertuples(index=False):
        signal_date = pd.Timestamp(period.signal_date)
        entry_date = pd.Timestamp(period.entry_date)
        exit_date = pd.Timestamp(period.exit_date)
        signal_key = signal_date.strftime("%Y%m%d")

        point_metadata = metadata[
            metadata["list_date"] < signal_key
        ][["ts_code", "name", "industry", "list_date"]]
        metrics = build_metrics_for_signal(
            history, signal_date, config, point_metadata, valuation
        )
        all_metrics.append(metrics)

        price_eligible: set[str] = set()
        for code in point_metadata["ts_code"]:
            price = prices.get(code)
            if price is None:
                continue
            if executable_return(
                price, entry_date, exit_date, config.cost_bps_each_side
            ) is not None:
                price_eligible.add(code)

        selections = select_strategies(metrics, price_eligible, config.top_k)
        for strategy, selected_codes in selections.items():
            path, trades = period_path(
                selected_codes,
                prices,
                trading_dates,
                entry_date,
                exit_date,
                config.cost_bps_each_side,
            )
            if path.empty or trades.empty:
                continue
            trades["strategy"] = strategy
            trades["signal_date"] = signal_date
            trade_frames.append(trades)
            period_rows.append(
                {
                    "strategy": strategy,
                    "signal_date": signal_date,
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "selected_count": len(selected_codes),
                    "executed_count": len(trades),
                    "gross_return": float(trades["gross_return"].mean()),
                    "net_return": float(trades["net_return"].mean()),
                    "selected_codes": ",".join(selected_codes),
                }
            )
            equity_parts.setdefault(strategy, []).append(path)

    metrics_frame = pd.concat(all_metrics, ignore_index=True)
    periods_frame = pd.DataFrame(period_rows)
    trades_frame = pd.concat(trade_frames, ignore_index=True)

    equity_columns: dict[str, pd.Series] = {}
    summary_rows: list[dict] = []
    for strategy, parts in equity_parts.items():
        level = 1.0
        stitched: list[pd.Series] = []
        first_date = parts[0].index[0]
        stitched.append(pd.Series([1.0], index=[first_date - pd.Timedelta(days=1)]))
        for index, part in enumerate(parts):
            normalized = part * level
            if index < len(parts) - 1:
                normalized = normalized.iloc[:-1]
            stitched.append(normalized)
            level = float(part.iloc[-1] * level)
        equity = pd.concat(stitched).sort_index()
        equity_columns[strategy] = equity
        strategy_periods = periods_frame[
            periods_frame["strategy"] == strategy
        ].sort_values("signal_date")
        summary_rows.append(summarize_equity(strategy, equity, strategy_periods))

    equity_frame = pd.DataFrame(equity_columns).sort_index()
    equity_frame.index.name = "Date"
    summary_frame = pd.DataFrame(summary_rows).sort_values(
        "annualized_return", ascending=False
    )
    factor_ic, quality_quintiles = factor_diagnostics(
        metrics_frame, trades_frame
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_csv(metrics_frame, OUTPUT_DIR / f"{output_prefix}_signal_metrics.csv")
    _write_csv(periods_frame, OUTPUT_DIR / f"{output_prefix}_periods.csv")
    _write_csv(trades_frame, OUTPUT_DIR / f"{output_prefix}_trades.csv")
    _write_csv(
        equity_frame.reset_index(), OUTPUT_DIR / f"{output_prefix}_equity.csv"
    )
    _write_csv(summary_frame, OUTPUT_DIR / f"{output_prefix}_summary.csv")
    _write_csv(factor_ic, OUTPUT_DIR / f"{output_prefix}_factor_ic.csv")
    _write_csv(
        quality_quintiles,
        OUTPUT_DIR / f"{output_prefix}_quality_quintiles.csv",
    )
    return (
        summary_frame,
        periods_frame,
        metrics_frame,
        equity_frame,
        factor_ic,
        quality_quintiles,
    )


def pct(value: float) -> str:
    return "NA" if pd.isna(value) else f"{value:.2%}"


def build_report(
    config: BacktestConfig,
    summary: pd.DataFrame,
    periods: pd.DataFrame,
    metrics: pd.DataFrame,
    factor_ic: pd.DataFrame,
    quality_quintiles: pd.DataFrame,
    output_prefix: str,
) -> str:
    benchmark = summary[summary["strategy"] == "benchmark_mainboard"]
    benchmark_row = benchmark.iloc[0] if not benchmark.empty else None

    lines = [
        "# A 股主板 ROE + 自由现金流策略实测报告",
        "",
        "> 本报告用于量化研究，不构成投资建议或收益承诺。",
        "",
        "## 测试口径",
        "",
        f"- 测试年度：{config.start_year} 至 {config.end_year}，每年 5 月年报披露期结束后调仓。",
        "- 信号日只使用该日之前已公告的财务报表，下一交易日开盘成交。",
        f"- 财务观察窗：最近 {config.lookback_years} 年，至少需要 {config.min_years} 年有效记录。",
        "- 自由现金流代理：经营活动现金流减购建固定资产、无形资产和其他长期资产支付的现金。",
        "- 排除银行、保险、证券等金融行业，避免杠杆和现金流口径不可比。",
        f"- 双边交易摩擦：每边 {config.cost_bps_each_side:.0f} bps。",
        "- 持有期一年，等权持仓，使用复权日线开盘价成交、收盘价盯市。",
        "",
        "严格质量门槛：",
        "",
        "- ROE 中位数不低于 15%，至少 4 年为正。",
        "- 自由现金流至少 4 年为正，最近窗口累计值为正。",
        f"- 最新资产负债率不高于 {config.max_debt_to_assets:.0f}%。",
        "",
        "## 汇总结果",
        "",
        "| 策略 | 年均持股 | 累计收益 | 年化收益 | 最大回撤 | Sharpe | Calmar | 年度胜率 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.itertuples(index=False):
        lines.append(
            f"| {row.strategy} | {row.avg_holdings:.1f} | "
            f"{pct(row.compound_return)} | {pct(row.annualized_return)} | "
            f"{pct(row.max_drawdown)} | {row.sharpe:.2f} | "
            f"{row.calmar:.2f} | {pct(row.period_win_rate)} |"
        )

    lines.extend(
        [
            "",
            "## 分年度结果",
            "",
            "| 策略 | 信号日 | 持股数 | 净收益 |",
            "|---|---|---:|---:|",
        ]
    )
    for row in periods.sort_values(["signal_date", "strategy"]).itertuples(index=False):
        lines.append(
            f"| {row.strategy} | {pd.Timestamp(row.signal_date).date()} | "
            f"{row.executed_count} | {pct(row.net_return)} |"
        )

    strict_counts = (
        metrics.groupby("signal_date")["strict_quality"].sum().sort_index()
    )
    lines.extend(
        [
            "",
            "## 候选数量",
            "",
            "| 信号日 | 严格通过数量 |",
            "|---|---:|",
        ]
    )
    for date, count in strict_counts.items():
        lines.append(f"| {pd.Timestamp(date).date()} | {int(count)} |")

    lines.extend(
        [
            "",
            "## 因子诊断",
            "",
            "| 指标 | 平均 Spearman IC | IC 为正比例 |",
            "|---|---:|---:|",
        ]
    )
    ic_summary = factor_ic.groupby("feature")["spearman_ic"].agg(
        mean_ic="mean",
        positive_rate=lambda values: float((values > 0).mean()),
    )
    for feature, row in ic_summary.sort_values("mean_ic", ascending=False).iterrows():
        lines.append(
            f"| {feature} | {row['mean_ic']:.4f} | "
            f"{row['positive_rate']:.2%} |"
        )

    lines.extend(
        [
            "",
            "质量分五分位中，1 代表最低、5 代表最高：",
            "",
            "| 质量五分位 | 六期累计收益 |",
            "|---:|---:|",
        ]
    )
    quintile_compound = quality_quintiles.groupby("quality_quintile")[
        "net_return"
    ].apply(lambda values: float((1.0 + values).prod() - 1.0))
    for quintile, compound in quintile_compound.items():
        lines.append(f"| {int(quintile)} | {pct(compound)} |")

    lines.extend(
        [
            "",
            "## 审计结论",
            "",
        ]
    )
    quality_name = f"roe_fcf_top{config.top_k}"
    quality = summary[summary["strategy"] == quality_name]
    if benchmark_row is not None and not quality.empty:
        quality_row = quality.iloc[0]
        annual_alpha = (
            quality_row["annualized_return"] - benchmark_row["annualized_return"]
        )
        dd_delta = quality_row["max_drawdown"] - benchmark_row["max_drawdown"]
        if annual_alpha > 0.02 and quality_row["sharpe"] > benchmark_row["sharpe"]:
            verdict = (
                "样本内相对基准表现为正，ROE 与自由现金流组合具有进一步研究价值，"
                "但还不足以直接实盘。"
            )
        elif annual_alpha > 0:
            verdict = (
                "组合略优于基准，但优势较弱，暂时只能视为质量过滤器，"
                "不能视为独立交易策略。"
            )
        else:
            verdict = (
                "组合未能战胜同口径主板等权基准，当前规则不具备独立交易价值。"
            )
        lines.extend(
            [
                f"- `{quality_name}` 相对基准年化差：{annual_alpha:+.2%}。",
                f"- 最大回撤差：{dd_delta:+.2%}，正值表示回撤较小。",
                f"- 结论：{verdict}",
            ]
        )

    lines.extend(
        [
            "",
            "## 重要限制",
            "",
            "- 当前股票池来自现存 `filtered_stocks.csv`，缺少历史退市股票，存在幸存者偏差。",
            "- 只有 6 个不重叠年度，统计把握度有限。",
            "- 年报统一延迟到 5 月使用可避免主要未来函数，但不能完全还原每份报告的市场预期。",
            "- 自由现金流采用标准代理口径，不同产业的资本开支性质仍有差异。",
            "- 结果使用日线数据，不能完整模拟停牌、涨跌停排队和真实冲击成本。",
            "",
            "实用判断应重点看相对同股票池基准的超额收益、回撤和跨年度稳定性，",
            "不能只看策略自身累计收益。",
            "",
            "## 结果文件",
            "",
            f"- `{output_prefix}_summary.csv`：策略汇总。",
            f"- `{output_prefix}_periods.csv`：分年度收益。",
            f"- `{output_prefix}_trades.csv`：逐股交易。",
            f"- `{output_prefix}_signal_metrics.csv`：每期财务指标与候选资格。",
            f"- `{output_prefix}_equity.csv`：日频组合净值。",
            f"- `{output_prefix}_factor_ic.csv`：逐年因子 IC。",
            f"- `{output_prefix}_quality_quintiles.csv`：质量分五分位收益。",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest the long-term ROE and free-cash-flow screen."
    )
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--lookback-years", type=int, default=5)
    parser.add_argument("--min-years", type=int, default=4)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--cost-bps-each-side", type=float, default=20.0)
    parser.add_argument("--max-debt-to-assets", type=float, default=70.0)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--max-codes", type=int)
    parser.add_argument("--force-collect", action="store_true")
    parser.add_argument("--output-prefix", default="full")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = BacktestConfig(
        start_year=args.start_year,
        end_year=args.end_year,
        lookback_years=args.lookback_years,
        min_years=args.min_years,
        top_k=args.top_k,
        cost_bps_each_side=args.cost_bps_each_side,
        max_debt_to_assets=args.max_debt_to_assets,
    )

    stocks = _read_csv(MAINBOARD_CSV, dtype={"ts_code": str, "list_date": str})
    industries = _read_csv(INDUSTRY_CSV, dtype={"ts_code": str})
    metadata = stocks[["ts_code", "name", "list_date"]].merge(
        industries[["ts_code", "industry"]], on="ts_code", how="left"
    )
    if args.max_codes:
        metadata = metadata.head(args.max_codes).copy()
    codes = metadata["ts_code"].dropna().unique().tolist()

    history = collect_financial_history(
        codes=codes,
        workers=args.workers,
        force=args.force_collect,
        quiet=args.quiet,
    )
    prices = load_prices(codes, workers=args.workers, quiet=args.quiet)
    schedule = build_schedule(trading_dates_from_prices(prices), config)
    valuation = collect_valuations(schedule, force=args.force_collect)

    summary, periods, metrics, _equity, factor_ic, quality_quintiles = run_backtest(
        config=config,
        metadata=metadata,
        history=history,
        valuation=valuation,
        prices=prices,
        output_prefix=args.output_prefix,
    )
    report = build_report(
        config=config,
        summary=summary,
        periods=periods,
        metrics=metrics,
        factor_ic=factor_ic,
        quality_quintiles=quality_quintiles,
        output_prefix=args.output_prefix,
    )
    report_path = OUTPUT_DIR / f"{args.output_prefix}_report.md"
    report_path.write_text(report, encoding="utf-8")
    print(summary.to_string(index=False))
    print(f"report: {report_path}")


if __name__ == "__main__":
    main()
