"""Dependency-free market-data providers for the live position monitor.

Both endpoints are free research-grade sources without an SLA.  The monitor
queries them independently and records source timestamps and price divergence.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Callable

try:
    from .core import DailyBar, Quote, SHANGHAI_TZ
except ImportError:  # direct execution/import from this directory
    from core import DailyBar, Quote, SHANGHAI_TZ


EASTMONEY_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={prefix}{code}"
SINA_QUOTE_URL = "https://hq.sinajs.cn/list={prefix}{code}"
EASTMONEY_UT = "7eea3edcaed734bea9cbfc24409ed989"


class MarketDataError(RuntimeError):
    """A provider returned unavailable or invalid market data."""


def normalize_symbol(symbol: str) -> tuple[str, str]:
    normalized = symbol.strip().upper().replace(":", ".")
    if normalized.startswith("SZ.") or normalized.startswith("SH."):
        exchange, code = normalized.split(".", 1)
    elif normalized.endswith(".SZ") or normalized.endswith(".SH"):
        code, exchange = normalized.split(".", 1)
    else:
        code = normalized
        exchange = "SH" if code.startswith("6") else "SZ"
    if not re.fullmatch(r"\d{6}", code) or exchange not in {"SH", "SZ"}:
        raise ValueError(f"Unsupported A-share symbol: {symbol!r}")
    return code, exchange


def _http_bytes(
    url: str, timeout: float, extra_headers: dict[str, str] | None = None
) -> bytes:
    headers = {
        "User-Agent": "Mozilla/5.0 stock-state-strategy-monitor/1.0",
        "Accept": "*/*",
    }
    headers.update(extra_headers or {})
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _with_retry(
    function: Callable[[], Any], retries: int, retry_delay_seconds: float
) -> Any:
    errors: list[str] = []
    for attempt in range(retries + 1):
        try:
            return function()
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            if attempt < retries:
                time.sleep(retry_delay_seconds * (2**attempt))
    raise MarketDataError("; ".join(errors))


def _eastmoney_price(value: Any, field: str) -> float:
    if value in (None, "", "-"):
        raise MarketDataError(f"Eastmoney field {field} is empty")
    number = float(value) / 100.0
    if number <= 0:
        raise MarketDataError(f"Eastmoney field {field} is not positive")
    return number


def parse_eastmoney_quote(payload: bytes | str, symbol: str) -> Quote:
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    document = json.loads(text)
    data = document.get("data")
    if document.get("rc") != 0 or not isinstance(data, dict):
        raise MarketDataError(f"Eastmoney quote response is invalid: rc={document.get('rc')}")
    code, exchange = normalize_symbol(symbol)
    returned_code = str(data.get("f57", ""))
    if returned_code != code:
        raise MarketDataError(
            f"Eastmoney returned code {returned_code!r}, expected {code!r}"
        )
    timestamp = datetime.fromtimestamp(int(data["f86"]), tz=SHANGHAI_TZ)
    return Quote(
        provider="eastmoney",
        symbol=f"{code}.{exchange}",
        name=str(data.get("f58") or ""),
        timestamp=timestamp,
        last_price=_eastmoney_price(data.get("f43"), "f43"),
        previous_close=_eastmoney_price(data.get("f60"), "f60"),
        open_price=_eastmoney_price(data.get("f46"), "f46"),
        high_price=_eastmoney_price(data.get("f44"), "f44"),
        low_price=_eastmoney_price(data.get("f45"), "f45"),
        volume=float(data["f47"]) if data.get("f47") not in (None, "-") else None,
        amount=float(data["f48"]) if data.get("f48") not in (None, "-") else None,
    )


def fetch_eastmoney_quote(symbol: str, timeout: float) -> Quote:
    code, exchange = normalize_symbol(symbol)
    secid = f"{1 if exchange == 'SH' else 0}.{code}"
    params = {
        "secid": secid,
        "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f86,f170",
    }
    url = f"{EASTMONEY_QUOTE_URL}?{urllib.parse.urlencode(params)}"
    return parse_eastmoney_quote(_http_bytes(url, timeout), symbol)


def parse_tencent_quote(payload: bytes | str, symbol: str) -> Quote:
    text = payload.decode("gbk", errors="replace") if isinstance(payload, bytes) else payload
    match = re.search(r'=\"(.*)\";', text.strip())
    if not match:
        raise MarketDataError("Tencent quote response has an unexpected envelope")
    parts = match.group(1).split("~")
    if len(parts) < 35:
        raise MarketDataError(f"Tencent quote has only {len(parts)} fields")
    code, exchange = normalize_symbol(symbol)
    if parts[2] != code:
        raise MarketDataError(f"Tencent returned code {parts[2]!r}, expected {code!r}")
    timestamp = datetime.strptime(parts[30], "%Y%m%d%H%M%S").replace(
        tzinfo=SHANGHAI_TZ
    )

    def price(index: int, label: str) -> float:
        try:
            value = float(parts[index])
        except (IndexError, ValueError) as exc:
            raise MarketDataError(f"Tencent field {label} is invalid") from exc
        if value <= 0:
            raise MarketDataError(f"Tencent field {label} is not positive")
        return value

    return Quote(
        provider="tencent",
        symbol=f"{code}.{exchange}",
        name=parts[1],
        timestamp=timestamp,
        last_price=price(3, "last"),
        previous_close=price(4, "previous_close"),
        open_price=price(5, "open"),
        high_price=price(33, "high"),
        low_price=price(34, "low"),
        volume=float(parts[6]) if parts[6] else None,
        amount=float(parts[37]) * 10000.0 if len(parts) > 37 and parts[37] else None,
    )


def fetch_tencent_quote(symbol: str, timeout: float) -> Quote:
    code, exchange = normalize_symbol(symbol)
    url = TENCENT_QUOTE_URL.format(prefix=exchange.lower(), code=code)
    return parse_tencent_quote(_http_bytes(url, timeout), symbol)


def parse_sina_quote(payload: bytes | str, symbol: str) -> Quote:
    text = payload.decode("gbk", errors="replace") if isinstance(payload, bytes) else payload
    match = re.search(r'=\"(.*)\";', text.strip())
    if not match:
        raise MarketDataError("Sina quote response has an unexpected envelope")
    parts = match.group(1).split(",")
    if len(parts) < 33:
        raise MarketDataError(f"Sina quote has only {len(parts)} fields")
    code, exchange = normalize_symbol(symbol)

    def price(index: int, label: str) -> float:
        try:
            value = float(parts[index])
        except (IndexError, ValueError) as exc:
            raise MarketDataError(f"Sina field {label} is invalid") from exc
        if value <= 0:
            raise MarketDataError(f"Sina field {label} is not positive")
        return value

    timestamp = datetime.strptime(
        f"{parts[30]} {parts[31]}", "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=SHANGHAI_TZ)
    return Quote(
        provider="sina",
        symbol=f"{code}.{exchange}",
        name=parts[0],
        timestamp=timestamp,
        last_price=price(3, "last"),
        previous_close=price(2, "previous_close"),
        open_price=price(1, "open"),
        high_price=price(4, "high"),
        low_price=price(5, "low"),
        volume=float(parts[8]) if parts[8] else None,
        amount=float(parts[9]) if parts[9] else None,
    )


def fetch_sina_quote(symbol: str, timeout: float) -> Quote:
    code, exchange = normalize_symbol(symbol)
    url = SINA_QUOTE_URL.format(prefix=exchange.lower(), code=code)
    payload = _http_bytes(
        url, timeout, {"Referer": "https://finance.sina.com.cn/"}
    )
    return parse_sina_quote(payload, symbol)


def fetch_quotes(
    symbol: str,
    *,
    provider_names: list[str] | None = None,
    timeout: float,
    retries: int,
    retry_delay_seconds: float,
) -> tuple[list[Quote], dict[str, str]]:
    all_providers: dict[str, Callable[[], Quote]] = {
        "eastmoney": lambda: _with_retry(
            lambda: fetch_eastmoney_quote(symbol, timeout),
            retries,
            retry_delay_seconds,
        ),
        "tencent": lambda: _with_retry(
            lambda: fetch_tencent_quote(symbol, timeout),
            retries,
            retry_delay_seconds,
        ),
        "sina": lambda: _with_retry(
            lambda: fetch_sina_quote(symbol, timeout),
            retries,
            retry_delay_seconds,
        ),
    }
    selected = provider_names or list(all_providers)
    unknown = sorted(set(selected) - set(all_providers))
    if unknown:
        raise ValueError(f"Unknown market-data providers: {', '.join(unknown)}")
    providers = {name: all_providers[name] for name in selected}
    quotes: list[Quote] = []
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(providers)) as executor:
        futures = {executor.submit(function): name for name, function in providers.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                quotes.append(future.result())
            except Exception as exc:
                errors[name] = f"{type(exc).__name__}: {exc}"
    quotes.sort(key=lambda quote: (quote.provider != "eastmoney", quote.provider))
    return quotes, errors


def parse_eastmoney_bars(payload: bytes | str, symbol: str) -> list[DailyBar]:
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    document = json.loads(text)
    data = document.get("data")
    if document.get("rc") != 0 or not isinstance(data, dict):
        raise MarketDataError("Eastmoney daily-bar response is invalid")
    code, _exchange = normalize_symbol(symbol)
    if str(data.get("code", code)) != code:
        raise MarketDataError("Eastmoney daily-bar symbol mismatch")
    bars: list[DailyBar] = []
    for raw in data.get("klines") or []:
        parts = raw.split(",")
        if len(parts) < 7:
            continue
        bar = DailyBar(
            trade_date=date.fromisoformat(parts[0]),
            open_price=float(parts[1]),
            close_price=float(parts[2]),
            high_price=float(parts[3]),
            low_price=float(parts[4]),
            volume=float(parts[5]),
            amount=float(parts[6]),
        )
        if min(
            bar.open_price, bar.close_price, bar.high_price, bar.low_price
        ) <= 0 or bar.high_price < bar.low_price:
            raise MarketDataError(f"Invalid daily OHLC on {bar.trade_date}")
        bars.append(bar)
    if not bars:
        raise MarketDataError("Eastmoney returned no daily bars")
    return sorted(bars, key=lambda bar: bar.trade_date)


def fetch_daily_bars(
    symbol: str,
    *,
    start_date: date,
    end_date: date,
    timeout: float,
    retries: int,
    retry_delay_seconds: float,
) -> list[DailyBar]:
    code, exchange = normalize_symbol(symbol)
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": EASTMONEY_UT,
        "klt": "101",
        "fqt": "1",
        "secid": f"{1 if exchange == 'SH' else 0}.{code}",
        "beg": start_date.strftime("%Y%m%d"),
        "end": end_date.strftime("%Y%m%d"),
    }
    url = f"{EASTMONEY_KLINE_URL}?{urllib.parse.urlencode(params)}"
    return _with_retry(
        lambda: parse_eastmoney_bars(_http_bytes(url, timeout), symbol),
        retries,
        retry_delay_seconds,
    )


def default_bar_window(entry_date: date, now_date: date) -> tuple[date, date]:
    return entry_date - timedelta(days=240), max(entry_date, now_date)


def load_local_adjusted_bars(
    market_root: str,
    symbol: str,
    *,
    start_date: date,
    end_date: date,
) -> list[DailyBar]:
    """Read adjusted OHLC from the project's validated local parquet store."""

    try:
        import pyarrow.compute as pc
        import pyarrow.dataset as ds
    except ImportError as exc:
        raise MarketDataError("pyarrow is required for local daily-bar fallback") from exc

    from pathlib import Path

    root = Path(market_root)
    code, exchange = normalize_symbol(symbol)
    instrument_id = f"CN.{'XSHG' if exchange == 'SH' else 'XSHE'}.{code}"
    price_root = root / "daily_prices"
    factor_root = root / "adjustment_factors"
    if not price_root.is_dir() or not factor_root.is_dir():
        raise MarketDataError(f"Local market-data root is incomplete: {root}")

    date_scalar_start = pc.scalar(start_date)
    date_scalar_end = pc.scalar(end_date)
    price_dataset = ds.dataset(price_root, format="parquet", partitioning="hive")
    factor_dataset = ds.dataset(factor_root, format="parquet", partitioning="hive")
    common_filter = (
        (ds.field("instrument_id") == instrument_id)
        & (ds.field("trade_date") >= date_scalar_start)
        & (ds.field("trade_date") <= date_scalar_end)
    )
    prices = price_dataset.to_table(
        columns=["trade_date", "open", "close", "high", "low", "volume_shares", "turnover_cny"],
        filter=common_filter,
    ).to_pandas()
    factors = factor_dataset.to_table(
        columns=["trade_date", "adjustment_factor"], filter=common_filter
    ).to_pandas()
    if prices.empty or factors.empty:
        raise MarketDataError(f"No local bars for {symbol} in requested range")
    merged = prices.merge(factors, on="trade_date", how="inner").sort_values("trade_date")
    if merged.empty:
        raise MarketDataError(f"Local price/factor join is empty for {symbol}")
    bars: list[DailyBar] = []
    for row in merged.itertuples(index=False):
        factor = float(row.adjustment_factor)
        bars.append(
            DailyBar(
                trade_date=row.trade_date,
                open_price=float(row.open) * factor,
                close_price=float(row.close) * factor,
                high_price=float(row.high) * factor,
                low_price=float(row.low) * factor,
                volume=float(row.volume_shares),
                amount=float(row.turnover_cny),
            )
        )
    return bars
