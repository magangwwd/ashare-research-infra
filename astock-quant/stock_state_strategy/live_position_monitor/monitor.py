"""Track one A-share position and email frozen-strategy exit alerts.

The program is alert-only.  It has no broker API and never submits an order.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import shutil
import socket
import smtplib
import ssl
import sys
import time as time_module
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

try:
    from .core import (
        AlertEvent,
        DailyBar,
        Quote,
        SHANGHAI_TZ,
        build_quote_bundle,
        calculate_frozen_stop,
        choose_strategy_events,
        evaluate_distribution_exit,
        evaluate_price_stop,
        evaluate_time_exit,
        make_fixed_stop,
        parse_clock,
        parse_date,
        position_fingerprint,
        scheduled_time_exit_date,
    )
    from .providers import (
        default_bar_window,
        fetch_daily_bars,
        fetch_quotes,
        load_local_adjusted_bars,
    )
except ImportError:  # direct: python monitor.py
    from core import (
        AlertEvent,
        DailyBar,
        Quote,
        SHANGHAI_TZ,
        build_quote_bundle,
        calculate_frozen_stop,
        choose_strategy_events,
        evaluate_distribution_exit,
        evaluate_price_stop,
        evaluate_time_exit,
        make_fixed_stop,
        parse_clock,
        parse_date,
        position_fingerprint,
        scheduled_time_exit_date,
    )
    from providers import (
        default_bar_window,
        fetch_daily_bars,
        fetch_quotes,
        load_local_adjusted_bars,
    )


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.json"


class ConfigurationError(RuntimeError):
    """The monitor configuration is missing or inconsistent."""


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ConfigurationError(f"Expected a JSON object in {path}")
    return value


def load_env_file(path: Path) -> list[str]:
    """Load a minimal KEY=VALUE file without logging or exposing secrets."""

    loaded: list[str] = []
    if not path.is_file():
        return loaded
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                raise ConfigurationError(
                    f"Invalid .env line {line_number}: expected KEY=VALUE"
                )
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or not key.replace("_", "").isalnum():
                raise ConfigurationError(f"Invalid .env key on line {line_number}")
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if key not in os.environ:
                os.environ[key] = value
            loaded.append(key)
    return loaded


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, default=str)
        handle.write("\n")
    os.replace(temporary, path)


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")


def resolve_path(config_path: Path, raw: str) -> Path:
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (config_path.parent / path).resolve()


def validate_config(config: dict[str, Any], config_path: Path) -> dict[str, Any]:
    position = config.get("position") or {}
    required = ("symbol", "name", "entry_price", "entry_date")
    missing = [field for field in required if position.get(field) in (None, "")]
    if missing:
        raise ConfigurationError(f"Missing position fields: {', '.join(missing)}")
    if float(position["entry_price"]) <= 0:
        raise ConfigurationError("entry_price must be positive")
    parse_date(str(position["entry_date"]))

    strategy_section = config.get("strategy") or {}
    strategy_path = resolve_path(config_path, str(strategy_section.get("strategy_file", "")))
    if not strategy_path.is_file():
        raise ConfigurationError(f"Strategy file does not exist: {strategy_path}")
    strategy = read_json(strategy_path)
    if strategy.get("strategy_id") != "LOCK_MARKUP_SINGLE_PAPER_V1":
        raise ConfigurationError("Unexpected strategy_id; refusing to mix exit rules")
    if strategy.get("status") != "PAPER_ONLY_NO_ENTRY_AUTHORIZATION":
        raise ConfigurationError("Unexpected strategy status")
    return strategy


def runtime_paths(config: dict[str, Any], config_path: Path) -> tuple[Path, Path]:
    runtime = config.get("runtime") or {}
    state_path = resolve_path(config_path, runtime.get("state_file", "runtime/state.json"))
    audit_path = resolve_path(
        config_path, runtime.get("audit_log", "runtime/monitor_audit.jsonl")
    )
    return state_path, audit_path


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "sent_events": {}}
    state = read_json(path)
    state.setdefault("schema_version", 1)
    state.setdefault("sent_events", {})
    return state


def reset_state(path: Path) -> Path | None:
    if not path.exists():
        return None
    timestamp = datetime.now(SHANGHAI_TZ).strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.stem}.{timestamp}.bak{path.suffix}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(backup))
    return backup


def position_identity(config: dict[str, Any]) -> tuple[str, str, float, date]:
    position = config["position"]
    return (
        str(position["symbol"]).upper(),
        str(position["name"]),
        float(position["entry_price"]),
        parse_date(str(position["entry_date"])),
    )


def ensure_position_matches_state(
    state: dict[str, Any], symbol: str, entry_date: date, entry_price: float
) -> str:
    fingerprint = position_fingerprint(symbol, entry_date, entry_price)
    existing = state.get("position_fingerprint")
    if existing and existing != fingerprint:
        raise ConfigurationError(
            "Position config changed after stop initialization. Run with --reset-state "
            "to archive the old state and explicitly reinitialize."
        )
    state["position_fingerprint"] = fingerprint
    return fingerprint


def filter_fresh_quotes(
    quotes: list[Quote], config: dict[str, Any], now: datetime
) -> tuple[list[Quote], dict[str, str]]:
    settings = config.get("market_data") or {}
    maximum_live_age = float(settings.get("max_live_quote_age_seconds", 180))
    maximum_closed_age = float(settings.get("max_closed_quote_age_hours", 96)) * 3600
    local_now = now.astimezone(SHANGHAI_TZ)
    clock = local_now.time()
    presumed_market_open = local_now.weekday() < 5 and (
        time(9, 25) <= clock <= time(11, 35)
        or time(12, 55) <= clock <= time(15, 5)
    )
    valid: list[Quote] = []
    rejected: dict[str, str] = {}
    for quote in quotes:
        age = (local_now - quote.timestamp.astimezone(SHANGHAI_TZ)).total_seconds()
        if age < -120:
            rejected[quote.provider] = f"quote timestamp is {abs(age):.0f}s in the future"
        elif presumed_market_open and (
            quote.trade_date != local_now.date() or age > maximum_live_age
        ):
            rejected[quote.provider] = f"live quote is stale by {age:.0f}s"
        elif not presumed_market_open and age > maximum_closed_age:
            rejected[quote.provider] = f"closed-market quote is stale by {age:.0f}s"
        else:
            valid.append(quote)
    return valid, rejected


def fetch_bars_for_position(
    config: dict[str, Any], symbol: str, entry_date: date, now_date: date
) -> tuple[list[DailyBar], str, str | None]:
    settings = config.get("market_data") or {}
    start_date, end_date = default_bar_window(entry_date, now_date)
    try:
        bars = fetch_daily_bars(
            symbol,
            start_date=start_date,
            end_date=end_date,
            timeout=float(settings.get("timeout_seconds", 8)),
            retries=int(settings.get("retries", 2)),
            retry_delay_seconds=float(settings.get("retry_delay_seconds", 0.5)),
        )
        return bars, "EASTMONEY_QFQ_DAILY_BARS_BEFORE_ENTRY", None
    except Exception as exc:
        remote_error = f"{type(exc).__name__}: {exc}"
    gold_override = os.environ.get("ASTOCK_MARKET_DATA_ROOT")
    local_root = gold_override or settings.get("local_market_data_root")
    if not local_root:
        raise RuntimeError(remote_error)
    resolved_root = (
        Path(os.path.expandvars(gold_override)).expanduser().resolve()
        if gold_override
        else resolve_path(Path(__file__).resolve(), str(local_root))
    )
    bars = load_local_adjusted_bars(
        str(resolved_root),
        symbol,
        start_date=start_date,
        end_date=end_date,
    )
    maximum_local_age = int(settings.get("local_bar_max_age_days", 4))
    local_age = (min(entry_date, now_date) - bars[-1].trade_date).days
    if local_age > maximum_local_age:
        raise RuntimeError(
            f"Remote bars failed ({remote_error}); local bars are stale by "
            f"{local_age} calendar days"
        )
    return bars, "LOCAL_GOLD_PRICE_X_ADJUSTMENT_FACTOR", remote_error


def initialize_frozen_stop(
    config: dict[str, Any],
    strategy: dict[str, Any],
    state: dict[str, Any],
    bars: list[DailyBar],
    bar_source: str,
    quotes: list[Quote],
    now: datetime,
) -> None:
    if state.get("frozen_stop"):
        return
    _symbol, _name, entry_price, entry_date = position_identity(config)
    stop_rule = strategy["protective_stop"]
    fixed_stop_pct = (config.get("strategy") or {}).get("fixed_stop_pct")
    if fixed_stop_pct is None:
        frozen = calculate_frozen_stop(
            bars,
            entry_date,
            entry_price,
            atr_window=int(stop_rule["atr_window"]),
            atr_multiplier=float(stop_rule["atr_multiplier"]),
            minimum_stop_pct=float(stop_rule["minimum_stop_pct"]),
            maximum_stop_pct=float(stop_rule["maximum_stop_pct"]),
            source=bar_source,
        )
    else:
        frozen = make_fixed_stop(
            entry_date,
            entry_price,
            float(fixed_stop_pct),
            float(stop_rule["minimum_stop_pct"]),
            float(stop_rule["maximum_stop_pct"]),
        )
    state["frozen_stop"] = frozen.to_dict()
    state["frozen_stop"]["frozen_at"] = now.isoformat()
    same_day_lows = [quote.low_price for quote in quotes if quote.trade_date == entry_date]
    state["entry_day_low_baseline"] = min(same_day_lows) if same_day_lows else None


def load_latest_transition(
    config: dict[str, Any], config_path: Path, symbol: str
) -> tuple[dict[str, str] | None, Path | None]:
    strategy_section = config.get("strategy") or {}
    paths = strategy_section.get("state_transition_files") or []
    best_row: dict[str, str] | None = None
    best_path: Path | None = None
    for raw in paths:
        path = resolve_path(config_path, str(raw))
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                row_symbol = str(row.get("vendor_symbol") or "").upper()
                instrument = str(row.get("instrument_id") or "")
                if row_symbol != symbol and not instrument.endswith(symbol.split(".")[0]):
                    continue
                if best_row is None or str(row.get("trade_date", "")) > str(
                    best_row.get("trade_date", "")
                ):
                    best_row = row
                    best_path = path
    return best_row, best_path


def operational_event(
    event_type: str,
    summary: str,
    action: str,
    details: dict[str, Any],
    now: datetime,
    key_suffix: str,
) -> AlertEvent:
    return AlertEvent(
        event_type=event_type,
        severity="WARNING",
        observed_at=now,
        action=action,
        summary=summary,
        details=details,
        dedupe_key=f"{event_type}:{key_suffix}",
    )


def state_stale_event(
    config: dict[str, Any], transition: dict[str, str] | None, now: datetime
) -> AlertEvent | None:
    if transition is None or not transition.get("trade_date"):
        return operational_event(
            "STATE_DATA_MISSING",
            "未找到香山股份日级状态记录",
            "更新股东披露、行情和状态转换数据后重新运行；期间派发退出不可判定",
            {},
            now,
            now.date().isoformat(),
        )
    latest = parse_date(transition["trade_date"])
    maximum_age = int((config.get("strategy") or {}).get("state_data_max_age_days", 3))
    age = (now.astimezone(SHANGHAI_TZ).date() - latest).days
    if age <= maximum_age:
        return None
    return operational_event(
        "STATE_DATA_STALE",
        "香山股份日级状态数据已过期",
        "更新状态定义与状态转换文件；过期期间不能可靠判断拉升转派发退出",
        {"latest_state_date": latest.isoformat(), "age_calendar_days": age},
        now,
        f"{latest.isoformat()}:{now.date().isoformat()}",
    )


def smtp_values(config: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    email_config = config.get("email") or {}
    if not bool(email_config.get("enabled", True)):
        return None, ["email.enabled=false"]
    env_names = email_config.get("environment_variables") or {}

    def env(field: str) -> str:
        name = str(env_names.get(field, ""))
        return os.environ.get(name, "").strip() if name else ""

    values: dict[str, Any] = {
        "host": env("host"),
        "port": int(env("port") or email_config.get("default_port", 465)),
        "username": env("username"),
        "password": env("password"),
        "from_address": env("from_address") or env("username"),
        "to_addresses": [item.strip() for item in env("to_addresses").split(",") if item.strip()],
        "security": (env("security") or email_config.get("default_security", "ssl")).lower(),
        "proxy_url": (
            env("proxy_url")
            or os.environ.get("HTTPS_PROXY", "").strip()
            or os.environ.get("HTTP_PROXY", "").strip()
        ),
    }
    missing = [
        field
        for field in ("host", "username", "password", "from_address")
        if not values[field]
    ]
    if not values["to_addresses"]:
        missing.append("to_addresses")
    if values["security"] not in {"ssl", "starttls", "plain"}:
        missing.append("security(ssl/starttls/plain)")
    return (None, missing) if missing else (values, [])


def open_http_proxy_tunnel(
    proxy_url: str, target_host: str, target_port: int, timeout: float
) -> socket.socket:
    """Open an HTTP CONNECT tunnel without exposing proxy credentials."""

    parsed = urlsplit(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError("SMTP proxy must be an http:// or https:// URL")
    proxy_port = parsed.port or (443 if parsed.scheme == "https" else 8080)
    tunnel: socket.socket = socket.create_connection(
        (parsed.hostname, proxy_port), timeout=timeout
    )
    try:
        tunnel.settimeout(timeout)
        if parsed.scheme == "https":
            tunnel = ssl.create_default_context().wrap_socket(
                tunnel, server_hostname=parsed.hostname
            )
        headers = [
            f"CONNECT {target_host}:{target_port} HTTP/1.1",
            f"Host: {target_host}:{target_port}",
        ]
        if parsed.username is not None:
            credentials = (
                f"{unquote(parsed.username)}:{unquote(parsed.password or '')}".encode()
            )
            encoded = base64.b64encode(credentials).decode("ascii")
            headers.append(f"Proxy-Authorization: Basic {encoded}")
        tunnel.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = tunnel.recv(4096)
            if not chunk:
                raise ConnectionError("SMTP proxy closed the CONNECT tunnel")
            response += chunk
            if len(response) > 65536:
                raise ConnectionError("SMTP proxy returned an oversized response")
        status_line = response.split(b"\r\n", 1)[0].decode("ascii", "replace")
        parts = status_line.split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit() or not 200 <= int(parts[1]) < 300:
            raise ConnectionError(f"SMTP proxy CONNECT failed: {status_line}")
        return tunnel
    except Exception:
        tunnel.close()
        raise


class ProxySMTP(smtplib.SMTP):
    def __init__(self, *args: Any, proxy_url: str, **kwargs: Any) -> None:
        self.proxy_url = proxy_url
        super().__init__(*args, **kwargs)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        return open_http_proxy_tunnel(self.proxy_url, host, port, timeout)


class ProxySMTP_SSL(smtplib.SMTP_SSL):
    def __init__(self, *args: Any, proxy_url: str, **kwargs: Any) -> None:
        self.proxy_url = proxy_url
        super().__init__(*args, **kwargs)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        tunnel = open_http_proxy_tunnel(self.proxy_url, host, port, timeout)
        return self.context.wrap_socket(tunnel, server_hostname=host)


def render_event_email(
    event: AlertEvent, config: dict[str, Any], state: dict[str, Any]
) -> tuple[str, str]:
    symbol, name, entry_price, entry_date = position_identity(config)
    stop = state.get("frozen_stop") or {}
    prefix = str((config.get("email") or {}).get("subject_prefix", "[股票策略提醒]"))
    subject = f"{prefix}[{event.severity}] {name} {event.summary}"
    detail_lines = "\n".join(
        f"- {key}: {value}" for key, value in event.details.items()
    )
    body = f"""策略退出告警（只读监控，不会自动下单）

事件：{event.summary}
级别：{event.severity}
观察时间：{event.observed_at.astimezone(SHANGHAI_TZ).isoformat()}
建议动作：{event.action}

持仓：{name} {symbol}
买入日期：{entry_date.isoformat()}
买入价：{entry_price:.3f} 元
冻结止损比例：{float(stop.get('stop_pct', 0.0)):.4%}
冻结止损价：{float(stop.get('stop_price', 0.0)):.3f} 元

事件明细：
{detail_lines or '- 无'}

策略：LOCK_MARKUP_SINGLE_PAPER_V1
边界：ALERT_ONLY_NO_ORDER_EXECUTION / PAPER_ONLY_NO_ENTRY_AUTHORIZATION

请人工核验实时行情、停牌、涨跌停和可卖状态后再作决定。
"""
    return subject, body


def send_email(settings: dict[str, Any], subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings["from_address"]
    message["To"] = ", ".join(settings["to_addresses"])
    message.set_content(body)
    context = ssl.create_default_context()
    proxy_url = str(settings.get("proxy_url") or "")
    if settings["security"] == "ssl":
        smtp_class = ProxySMTP_SSL if proxy_url else smtplib.SMTP_SSL
        smtp_kwargs: dict[str, Any] = {"proxy_url": proxy_url} if proxy_url else {}
        with smtp_class(
            settings["host"],
            settings["port"],
            timeout=20,
            context=context,
            **smtp_kwargs,
        ) as smtp:
            smtp.login(settings["username"], settings["password"])
            smtp.send_message(message)
    else:
        smtp_class = ProxySMTP if proxy_url else smtplib.SMTP
        smtp_kwargs = {"proxy_url": proxy_url} if proxy_url else {}
        with smtp_class(
            settings["host"], settings["port"], timeout=20, **smtp_kwargs
        ) as smtp:
            if settings["security"] == "starttls":
                smtp.starttls(context=context)
            if settings["username"]:
                smtp.login(settings["username"], settings["password"])
            smtp.send_message(message)


def deliver_events(
    events: list[AlertEvent],
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    dry_run: bool,
) -> list[dict[str, Any]]:
    settings, missing = smtp_values(config)
    outcomes: list[dict[str, Any]] = []
    sent_events = state.setdefault("sent_events", {})
    for event in events:
        if event.dedupe_key in sent_events:
            outcomes.append({"event": event.event_type, "status": "ALREADY_SENT"})
            continue
        subject, body = render_event_email(event, config, state)
        if dry_run:
            print("\n--- DRY-RUN EMAIL ---")
            print(f"Subject: {subject}\n\n{body}")
            outcomes.append({"event": event.event_type, "status": "DRY_RUN"})
            continue
        if settings is None:
            outcomes.append(
                {
                    "event": event.event_type,
                    "status": "EMAIL_NOT_CONFIGURED",
                    "missing": missing,
                }
            )
            continue
        try:
            send_email(settings, subject, body)
        except Exception as exc:
            outcomes.append(
                {
                    "event": event.event_type,
                    "status": "SEND_FAILED",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            sent_at = datetime.now(SHANGHAI_TZ).isoformat()
            sent_events[event.dedupe_key] = sent_at
            outcomes.append(
                {"event": event.event_type, "status": "SENT", "sent_at": sent_at}
            )
    return outcomes


def simulation_quotes(
    symbol: str, name: str, price: float, now: datetime
) -> list[Quote]:
    if price <= 0:
        raise ValueError("--simulate-price must be positive")
    return [
        Quote(
            provider=provider,
            symbol=symbol,
            name=name,
            timestamp=now,
            last_price=price,
            previous_close=price,
            open_price=price,
            high_price=price,
            low_price=price,
            volume=1.0,
            amount=price,
        )
        for provider in ("eastmoney", "tencent")
    ]


def run_cycle(
    config: dict[str, Any],
    strategy: dict[str, Any],
    config_path: Path,
    *,
    initialize: bool,
    dry_run: bool,
    simulate_price: float | None,
    simulate_transition: str | None,
) -> dict[str, Any]:
    now = datetime.now(SHANGHAI_TZ)
    symbol, name, entry_price, entry_date = position_identity(config)
    state_path, audit_path = runtime_paths(config, config_path)
    state = load_state(state_path)
    ensure_position_matches_state(state, symbol, entry_date, entry_price)

    provider_errors: dict[str, str] = {}
    if simulate_price is not None:
        raw_quotes = simulation_quotes(symbol, name, simulate_price, now)
    else:
        settings = config.get("market_data") or {}
        raw_quotes, provider_errors = fetch_quotes(
            symbol,
            provider_names=list(settings.get("providers") or []),
            timeout=float(settings.get("timeout_seconds", 8)),
            retries=int(settings.get("retries", 2)),
            retry_delay_seconds=float(settings.get("retry_delay_seconds", 0.5)),
        )
    quotes, stale_errors = filter_fresh_quotes(raw_quotes, config, now)
    provider_errors.update(stale_errors)
    if not quotes:
        raise RuntimeError(f"No fresh quotes: {provider_errors}")

    bundle = build_quote_bundle(
        quotes,
        float((config.get("market_data") or {}).get("max_cross_source_diff_pct", 0.003)),
    )

    bars: list[DailyBar] = []
    bar_error: str | None = None
    bar_source: str | None = None
    try:
        bars, bar_source, bar_error = fetch_bars_for_position(
            config, symbol, entry_date, now.date()
        )
    except Exception as exc:
        bar_error = f"{type(exc).__name__}: {exc}"
        if not state.get("frozen_stop"):
            raise

    if not state.get("frozen_stop") or initialize:
        initialize_frozen_stop(config, strategy, state, bars, bar_source or "UNKNOWN", quotes, now)
    frozen = state["frozen_stop"]

    transition, transition_path = load_latest_transition(config, config_path, symbol)
    if simulate_transition:
        transition = {
            "vendor_symbol": symbol,
            "trade_date": now.date().isoformat(),
            "transition": simulate_transition,
            "defined_state": "distribution",
            "pool_status": "B_MONITOR_ONLY",
            "confidence": "90",
        }
        transition_path = None

    price_event = evaluate_price_stop(
        bundle,
        entry_date=entry_date,
        entry_price=entry_price,
        stop_price=float(frozen["stop_price"]),
        entry_day_low_baseline=state.get("entry_day_low_baseline"),
        now=now,
    )
    distribution_event = evaluate_distribution_exit(
        transition, symbol=symbol, entry_date=entry_date, now=now
    )
    time_exit_date = scheduled_time_exit_date(
        bars,
        entry_date,
        int(strategy["time_exit"]["maximum_forward_sessions_after_entry"]),
    ) if bars else None
    time_event = evaluate_time_exit(
        time_exit_date,
        symbol=symbol,
        now=now,
        market_close=parse_clock(
            str((config.get("monitor") or {}).get("market_close_time", "15:00"))
        ),
    )
    events = choose_strategy_events(price_event, distribution_event, time_event)
    stale_state = state_stale_event(config, transition, now)
    if stale_state is not None and simulate_transition is None:
        events.append(stale_state)

    outcomes = deliver_events(events, config, state, dry_run=dry_run)
    summary = {
        "observed_at": now.isoformat(),
        "mode": "SIMULATION" if simulate_price is not None or simulate_transition else "LIVE",
        "execution_authority": "ALERT_ONLY_NO_ORDER_EXECUTION",
        "position": {
            "symbol": symbol,
            "name": name,
            "entry_date": entry_date.isoformat(),
            "entry_price": entry_price,
            "current_price": bundle.primary.last_price,
            "pnl_pct_before_cost": bundle.primary.last_price / entry_price - 1.0,
        },
        "frozen_stop": frozen,
        "distance_above_stop_pct": bundle.primary.last_price / float(frozen["stop_price"]) - 1.0,
        "quote_quality": {
            "source_count": bundle.source_count,
            "sources_agree": bundle.sources_agree,
            "cross_source_diff_pct": bundle.cross_source_diff_pct,
            "provider_errors": provider_errors,
        },
        "transition_source": str(transition_path) if transition_path else None,
        "latest_transition": transition,
        "scheduled_time_exit_date": time_exit_date.isoformat() if time_exit_date else None,
        "bar_error": bar_error,
        "bar_source": bar_source,
        "events": [event.to_dict() for event in events],
        "email_outcomes": outcomes,
        "decision": "EXIT_ALERT" if any(
            event.event_type in {
                "PRICE_STOP_CONFIRMED",
                "MARKUP_TO_DISTRIBUTION_EXIT",
                "MAX_HOLDING_TIME_EXIT",
            }
            for event in events
        ) else "HOLD_AND_MONITOR",
    }

    if simulate_price is None and simulate_transition is None:
        state["last_live_run"] = summary
        latest_status_path = resolve_path(
            config_path,
            (config.get("runtime") or {}).get(
                "latest_status_file", "runtime/latest_status.json"
            ),
        )
        atomic_write_json(latest_status_path, summary)
        atomic_write_json(state_path, state)
    elif initialize:
        # Initialization is intentionally persisted even when the email is a
        # dry run; simulated market events never contaminate live state.
        state["initialized_at"] = now.isoformat()
        atomic_write_json(state_path, state)
    append_jsonl(audit_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return summary


def send_test_email(config: dict[str, Any], state: dict[str, Any]) -> None:
    settings, missing = smtp_values(config)
    if settings is None:
        raise ConfigurationError(f"SMTP environment is incomplete: {', '.join(missing)}")
    now = datetime.now(SHANGHAI_TZ)
    event = operational_event(
        "SMTP_TEST",
        "股票监控邮件通道测试成功",
        "无需交易操作",
        {"purpose": "verify SMTP delivery only"},
        now,
        now.isoformat(),
    )
    subject, body = render_event_email(event, config, state)
    send_email(settings, subject, body)
    print("Test email sent successfully.")


def config_report(config: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    symbol, name, entry_price, entry_date = position_identity(config)
    settings, missing = smtp_values(config)
    return {
        "valid": True,
        "position": {
            "symbol": symbol,
            "name": name,
            "entry_price": entry_price,
            "entry_date": entry_date.isoformat(),
            "entry_date_note": config["position"].get("entry_date_note"),
        },
        "strategy_id": strategy.get("strategy_id"),
        "strategy_status": strategy.get("status"),
        "smtp_ready": settings is not None,
        "smtp_missing": missing,
        "broker_order_capability": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--initialize", action="store_true")
    action.add_argument("--loop", action="store_true")
    action.add_argument("--test-email", action="store_true")
    action.add_argument("--check-config", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="render email but do not send")
    parser.add_argument("--reset-state", action="store_true")
    parser.add_argument("--simulate-price", type=float)
    parser.add_argument(
        "--simulate-transition",
        choices=("none", "lock_to_markup", "markup_to_distribution"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    config = read_json(config_path)
    email_config = config.get("email") or {}
    env_file = email_config.get("env_file")
    if env_file:
        load_env_file(resolve_path(config_path, str(env_file)))
    strategy = validate_config(config, config_path)
    state_path, audit_path = runtime_paths(config, config_path)
    if args.reset_state:
        backup = reset_state(state_path)
        print(f"Previous state archived to: {backup}" if backup else "No state to reset.")
    if args.check_config:
        print(json.dumps(config_report(config, strategy), ensure_ascii=False, indent=2))
        return 0
    if args.test_email:
        send_test_email(config, load_state(state_path))
        return 0

    def cycle() -> dict[str, Any] | None:
        try:
            return run_cycle(
                config,
                strategy,
                config_path,
                initialize=args.initialize,
                dry_run=args.dry_run,
                simulate_price=args.simulate_price,
                simulate_transition=args.simulate_transition,
            )
        except Exception as exc:
            now = datetime.now(SHANGHAI_TZ)
            failure = {
                "observed_at": now.isoformat(),
                "status": "MONITOR_CYCLE_FAILED",
                "error": f"{type(exc).__name__}: {exc}",
            }
            append_jsonl(audit_path, failure)
            print(json.dumps(failure, ensure_ascii=False, indent=2), file=sys.stderr)
            return None

    if not args.loop:
        result = cycle()
        return 0 if result is not None else 1

    monitor_config = config.get("monitor") or {}
    live_interval = max(10, int(monitor_config.get("poll_interval_seconds", 30)))
    closed_interval = max(60, int(monitor_config.get("closed_poll_interval_seconds", 900)))
    print("Monitor loop started. Press Ctrl+C to stop.")
    try:
        while True:
            cycle()
            local_now = datetime.now(SHANGHAI_TZ)
            clock = local_now.time()
            market_window = local_now.weekday() < 5 and (
                time(9, 25) <= clock <= time(11, 35)
                or time(12, 55) <= clock <= time(15, 5)
            )
            time_module.sleep(live_interval if market_window else closed_interval)
    except KeyboardInterrupt:
        print("Monitor loop stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
