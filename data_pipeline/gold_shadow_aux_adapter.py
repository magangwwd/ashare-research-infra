"""Strict offline Task01 -> Gold auxiliary shadow candidates; never publish.

Mapping intentionally refuses to certify PIT membership, corporate actions, or
Gold daily_metrics limit_status (needs a separate point-in-time daily_limits join).
The inputs must have independent Drive SHA verification before calling.
"""
from __future__ import annotations

from data_pipeline.gold_shadow_daily_adapter import _date, _number, _exact_units


_METRICS = {
    "turnover_rate": "turnover_rate_pct",
    "turnover_rate_f": "free_float_turnover_rate_pct",
    "volume_ratio": "volume_ratio",
    "pe": "price_earnings",
    "pe_ttm": "price_earnings_ttm",
    "pb": "price_book",
    "ps": "price_sales",
    "ps_ttm": "price_sales_ttm",
    "dv_ratio": "dividend_yield_pct",
    "dv_ttm": "dividend_yield_ttm_pct",
}
_QUANTITIES = {
    "total_share": "total_shares",
    "float_share": "float_shares",
    "free_share": "free_float_shares",
    "total_mv": "market_cap_cny",
    "circ_mv": "float_market_cap_cny",
}
_BASE = {"ts_code", "security_id", "trade_date"}


def _prepare(rows, instruments, *, task_as_of, instruments_as_of, required):
    cutoff = _date(task_as_of, "task_as_of")
    if _date(instruments_as_of, "instruments_as_of") != cutoff:
        raise ValueError("PIT_UNIVERSE_DATE_MISMATCH")
    if not isinstance(rows, list) or not isinstance(instruments, list):
        raise ValueError("rows/instruments must be lists")
    mapping, used_ids = {}, set()
    for row in instruments:
        if not isinstance(row, dict):
            raise ValueError("invalid Gold instrument")
        symbol, ident = row.get("vendor_symbol"), row.get("instrument_id")
        if not isinstance(symbol, str) or not symbol or not isinstance(ident, str) or not ident:
            raise ValueError("invalid Gold symbol/instrument_id")
        if _date(row.get("as_of_date"), "instrument.as_of_date") != cutoff:
            raise ValueError("PIT_UNIVERSE_ROW_DATE_MISMATCH")
        if symbol in mapping or ident in used_ids:
            raise ValueError("duplicate Gold symbol/instrument_id")
        mapping[symbol] = ident
        used_ids.add(ident)

    seen, src_id_by_symbol, symbol_by_src_id = set(), {}, {}
    eligible = []
    excluded = 0
    for row in rows:
        if not isinstance(row, dict) or required - row.keys():
            raise ValueError("Task01 row missing required fields")
        sym, src_id = row["ts_code"], row["security_id"]
        if not isinstance(sym, str) or not sym or not isinstance(src_id, str) or not src_id:
            raise ValueError("invalid Task01 identity")
        if sym in src_id_by_symbol and src_id_by_symbol[sym] != src_id:
            raise ValueError("conflicting Task01 security_id")
        if src_id in symbol_by_src_id and symbol_by_src_id[src_id] != sym:
            raise ValueError("Task01 security_id reused")
        src_id_by_symbol[sym] = src_id
        symbol_by_src_id[src_id] = sym
        trade_day = _date(row["trade_date"], "trade_date")
        if trade_day > cutoff:
            raise ValueError("future Task01 row")
        key = (sym, trade_day)
        if key in seen:
            raise ValueError("duplicate Task01 key")
        seen.add(key)
        if sym not in mapping:
            excluded += 1
            continue
        eligible.append((row, mapping[sym], trade_day.isoformat()))
    if not eligible:
        raise ValueError("no overlapping shadow rows")
    return eligible, excluded


def _audit(source, converted, excluded, *, needs_limits):
    return {
        "status": "SHADOW_CANDIDATE_ONLY", "source_category": source,
        "converted_rows": converted, "excluded_outside_explicit_gold_universe": excluded,
        "point_in_time_membership_verified": False,
        "parquet_values_compared_to_gold": False,
        "quality_validation_complete": False,
        "percentage_scales_verified_against_gold": False,
        "requires_daily_limits_join": needs_limits,
        "corporate_actions_verified": False,
        "production_switch_allowed": False,
    }


def adapt_daily_metrics_shadow(rows, instruments, *, task_as_of, instruments_as_of):
    """Candidate mapping from Task01 daily_basic; missing limit_status is explicit."""
    required = _BASE | {"close"} | set(_METRICS) | set(_QUANTITIES)
    eligible, excluded = _prepare(
        rows, instruments, task_as_of=task_as_of,
        instruments_as_of=instruments_as_of, required=required,
    )
    output = []
    for src, instrument_id, day in eligible:
        price = _number(src["close"], "close")
        if price <= 0:
            raise ValueError("invalid close")
        item = {
            "instrument_id": instrument_id, "vendor_symbol": src["ts_code"],
            "trade_date": day, "close": float(price),
        }
        for raw, gold in _METRICS.items():
            value = src[raw]
            item[gold] = float(_number(value, raw)) if value is not None else None
        for raw, gold in _QUANTITIES.items():
            value = src[raw]
            item[gold] = _exact_units(value, 10000, raw) if value is not None else None
        # Task01 daily_basic alone has no Gold limit_status. Do not invent one.
        item.update(limit_status=None, currency="CNY", source_vendor="TUSHARE")
        output.append(item)
    output.sort(key=lambda x: (x["trade_date"], x["vendor_symbol"]))
    return output, _audit("daily_basic", len(output), excluded, needs_limits=True)


def adapt_adj_factor_shadow(rows, instruments, *, task_as_of, instruments_as_of):
    """Candidate unadjusted Tushare factor; factor convention not yet verified."""
    eligible, excluded = _prepare(
        rows, instruments, task_as_of=task_as_of, instruments_as_of=instruments_as_of,
        required=_BASE | {"adj_factor"},
    )
    output = []
    for src, instrument_id, day in eligible:
        factor = _number(src["adj_factor"], "adj_factor")
        if factor <= 0:
            raise ValueError("invalid adj_factor")
        output.append({
            "instrument_id": instrument_id, "vendor_symbol": src["ts_code"],
            "trade_date": day, "adjustment_factor": float(factor),
            "source_vendor": "TUSHARE",
        })
    output.sort(key=lambda x: (x["trade_date"], x["vendor_symbol"]))
    return output, _audit("adj_factor", len(output), excluded, needs_limits=False)
