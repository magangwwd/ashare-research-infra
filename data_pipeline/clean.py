"""Explicit, versioned schemas. Provider additions never enter Clean implicitly."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import NAMESPACE_URL, uuid5

import pandas as pd
import pyarrow as pa

SCHEMA_VERSION = "1.0.0"
SCHEMA_VERSIONS = {"index_basic": "2.0.0"}


def schema_version(dataset: str) -> str:
    return SCHEMA_VERSIONS.get(dataset, SCHEMA_VERSION)


@dataclass(frozen=True)
class Field:
    source: str
    name: str
    dtype: str
    description: str


def fields(names: str, dtype: str = "float64") -> list[Field]:
    return [Field(n, n, dtype, f"Tushare 原字段 {n}; 口径及单位遵循接口文档") for n in names.split()]


IDENTITY = [Field("ts_code", "ts_code", "string", "Tushare 证券标识"),
            Field("security_id", "security_id", "string", "基于证券标识的稳定 UUID5")]
TRADE = IDENTITY + [Field("trade_date", "trade_date", "date", "交易日期")]
FIN = IDENTITY + [Field("end_date", "report_period", "date", "财务报告期间"),
                  Field("ann_date", "ann_date", "date", "公告日期"),
                  Field("f_ann_date", "actual_ann_date", "date", "实际公告日期"),
                  Field("available_date", "available_date", "date", "预留; Task 01 不推定可用时点"),
                  *fields("report_type comp_type update_flag", "string")]
SCHEMAS: dict[str, list[Field]] = {
    "stock_basic": IDENTITY + fields("symbol name area industry market exchange list_status", "string")
        + [Field("list_date", "list_date", "date", "上市日期"),
           Field("delist_date", "delist_date", "date", "退市日期")],
    "security_master": IDENTITY + fields("symbol exchange name market board security_type listing_status st_raw_name industry", "string")
        + [Field("list_date", "list_date", "date", "上市日期"),
           Field("delist_date", "delist_date", "date", "退市日期"),
           Field("last_updated_at", "last_updated_at", "datetime", "证券主表更新时间")],
    "trade_cal": fields("exchange", "string") + [Field("cal_date", "calendar_date", "date", "自然日期"),
        Field("is_open", "is_open", "int64", "是否交易日"),
        Field("pretrade_date", "previous_trade_date", "date", "上一交易日期")],
    "daily": TRADE + fields("open high low close pre_close change pct_chg vol amount"),
    "stk_mins": TRADE + [Field("trade_time", "trade_time", "string", "A股15分钟K线结束时间，Asia/Shanghai本地时间"),
        Field("freq", "freq", "string", "固定为15min")] + fields("open high low close vol amount"),
    "adj_factor": TRADE + fields("adj_factor"),
    "daily_basic": TRADE + fields("close turnover_rate turnover_rate_f volume_ratio pe pe_ttm pb ps ps_ttm dv_ratio dv_ttm total_share float_share free_share total_mv circ_mv"),
    "income": FIN + fields("basic_eps diluted_eps total_revenue revenue int_income prem_earned comm_income n_income n_income_attr_p total_profit income_tax operate_profit total_cogs oper_cost sell_exp admin_exp fin_exp rd_exp"),
    "balancesheet": FIN + fields("total_share cap_rese undistr_porfit surplus_rese money_cap total_cur_assets total_nca total_assets total_cur_liab total_ncl total_liab total_hldr_eqy_exc_min_int total_hldr_eqy_inc_min_int goodwill inventories accounts_receiv"),
    "cashflow": FIN + fields("net_profit n_cashflow_act n_cashflow_inv n_cash_flows_fnc c_cash_equ_end_period c_cash_equ_beg_period c_recp_sell_goods c_paid_goods_s c_pay_acq_const_fiolta"),
    "fina_indicator": FIN + fields("eps dt_eps total_revenue_ps revenue_ps capital_rese_ps surplus_rese_ps undist_profit_ps extra_item profit_dedt gross_margin current_ratio quick_ratio cash_ratio roe roa roic netprofit_yoy or_yoy assets_yoy debt_to_assets"),
    "forecast": FIN + fields("type summary change_reason", "string") + fields("p_change_min p_change_max net_profit_min net_profit_max last_parent_net"),
    "express": FIN + fields("revenue operate_profit total_profit n_income total_assets total_hldr_eqy_exc_min_int diluted_eps diluted_roe yoy_net_profit"),
    "dividend": FIN + fields("div_proc", "string") + fields("stk_div stk_bo_rate stk_co_rate cash_div cash_div_tax base_share")
        + [Field(x, x, "date", "分红事件日期") for x in ("record_date", "ex_date", "pay_date", "div_listdate")],
    "namechange": IDENTITY + fields("name change_reason", "string")
        + [Field(x, x, "date", "名称历史日期") for x in ("start_date", "end_date", "ann_date")],
    "stock_st": TRADE + fields("name type type_name", "string"),
    "suspend_d": TRADE + fields("suspend_timing suspend_type", "string"),
    "stk_limit": TRADE + fields("pre_close up_limit down_limit"),
    "stk_premarket": TRADE + fields("total_share float_share pre_close up_limit down_limit"),
    "index_basic": fields("ts_code name fullname market publisher index_type category", "string")
        + [Field("base_date", "base_date", "date", "指数基日"), Field("list_date", "list_date", "date", "指数发布日期")]
        + fields("base_point"),
    "index_daily": fields("ts_code", "string") + [Field("trade_date", "trade_date", "date", "交易日期")] + fields("open high low close pre_close change pct_chg vol amount"),
    "index_weight": fields("index_code con_code", "string") + [Field("trade_date", "trade_date", "date", "成分观测日期")] + fields("weight"),
    "index_classify": fields("index_code industry_name level industry_code src is_pub parent_code", "string"),
    "index_member_all": fields("l1_code l1_name l2_code l2_name l3_code l3_name ts_code name is_new", "string")
        + [Field("in_date", "in_date", "date", "入选日期"), Field("out_date", "out_date", "date", "退出日期")],
    "ci_index_member": fields("l1_code l1_name l2_code l2_name l3_code l3_name ts_code name is_new", "string")
        + [Field("in_date", "in_date", "date", "入选日期"), Field("out_date", "out_date", "date", "退出日期")],
    "sw_daily": fields("ts_code name", "string") + [Field("trade_date", "trade_date", "date", "交易日期")] + fields("open high low close change pct_change vol amount pe pb float_mv total_mv"),
    "ci_daily": fields("ts_code", "string") + [Field("trade_date", "trade_date", "date", "交易日期")] + fields("open high low close pre_close change pct_change vol amount"),
    "margin": fields("exchange_id", "string") + [Field("trade_date", "trade_date", "date", "交易日期")] + fields("rzye rzmre rzche rqye rqmcl rqyl rzrqye"),
    "margin_detail": TRADE + fields("rzye rzmre rzche rqye rqmcl rqchl rqyl rzrqye"),
    "block_trade": TRADE + fields("price vol amount") + fields("buyer seller", "string"),
    "top_list": TRADE + fields("name reason", "string") + fields("close pct_change turnover_rate amount l_sell l_buy l_amount net_amount net_rate amount_rate float_values"),
    "stk_holdernumber": FIN + fields("holder_num"),
    "stk_holdertrade": IDENTITY + [Field("ann_date", "ann_date", "date", "公告日期"), Field("begin_date", "begin_date", "date", "开始日期"), Field("close_date", "close_date", "date", "截止日期")]
        + fields("holder_name holder_type in_de", "string") + fields("change_vol change_ratio after_share after_ratio avg_price total_share"),
    "stk_surv": IDENTITY + [Field("surv_date", "surv_date", "date", "调研日期"), Field("ann_date", "ann_date", "date", "公告日期")]
        + fields("name fund_visitors rece_place rece_mode rece_org org_type comp_rece content", "string"),
    "share_float": IDENTITY + [Field("ann_date", "ann_date", "date", "公告日期"), Field("float_date", "float_date", "date", "解禁日期")]
        + fields("float_share float_ratio") + fields("holder_name share_type", "string"),
}
KEYS = {
    "stock_basic": ["security_id"], "security_master": ["security_id"],
    "trade_cal": ["exchange", "calendar_date"],
    **{n: ["security_id", "trade_date"] for n in ("daily", "adj_factor", "daily_basic", "stk_premarket", "stk_limit", "margin_detail")},
    "stk_mins": ["security_id", "trade_time"],
    "stock_st": ["security_id", "trade_date", "type"],
    **{n: ["security_id", "report_period", "report_type", "comp_type"] for n in ("income", "balancesheet", "cashflow")},
    **{n: ["security_id", "report_period"] for n in ("fina_indicator", "express", "stk_holdernumber")},
    "forecast": ["security_id", "report_period", "type"],
    "dividend": ["security_id", "report_period", "div_proc"],
    "namechange": ["security_id", "start_date", "name"],
    "suspend_d": ["security_id", "trade_date", "suspend_type", "suspend_timing"],
    "index_basic": ["ts_code"], "index_daily": ["ts_code", "trade_date"],
    "index_weight": ["index_code", "con_code", "trade_date"],
    "index_classify": ["src", "index_code"],
    "index_member_all": ["ts_code", "l3_code", "in_date"],
    "ci_index_member": ["ts_code", "l3_code", "in_date"],
    "sw_daily": ["ts_code", "trade_date"], "ci_daily": ["ts_code", "trade_date"],
    "margin": ["exchange_id", "trade_date"],
    "block_trade": ["security_id", "trade_date", "price", "vol", "buyer", "seller"],
    "top_list": ["security_id", "trade_date", "reason"],
    "stk_holdertrade": ["security_id", "ann_date", "holder_name", "begin_date", "close_date", "in_de"],
    "stk_surv": ["security_id", "surv_date", "fund_visitors", "rece_org"],
    "share_float": ["security_id", "ann_date", "float_date", "holder_name"],
}
FINANCIAL = {"income", "balancesheet", "cashflow", "fina_indicator", "forecast", "express", "dividend", "stk_holdernumber"}
LONG_RAW = set(SCHEMAS) - {"daily", "daily_basic", "adj_factor"}


def security_id(ts_code: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"tushare:security:{ts_code}"))


def convert_dates(values: pd.Series) -> pd.Series:
    # Reject nonblank invalid dates instead of quietly dropping them.
    normalized = values.astype("string").str.strip().replace({"": pd.NA})
    compact = normalized.str.fullmatch(r"\d{8}").fillna(False)
    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    result.loc[compact] = pd.to_datetime(normalized.loc[compact], format="%Y%m%d", errors="raise")
    result.loc[~compact] = pd.to_datetime(normalized.loc[~compact], errors="raise")
    return result.dt.date.astype("object").where(result.notna(), None)


def security_master(raw: pd.DataFrame, now: str) -> pd.DataFrame:
    result = raw.copy()
    # Board is based on provider market metadata, never numeric code prefixes.
    result["board"] = result["market"].map({"主板": "MAIN", "创业板": "CHINEXT", "科创板": "STAR", "北交所": "BSE"}).fillna("UNKNOWN")
    result["exchange"] = result["exchange"].replace("", pd.NA).fillna(result["ts_code"].str.rsplit(".", n=1).str[-1].map({"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}))
    result["security_type"] = "A_SHARE"  # stock_basic is the A-share endpoint.
    result["listing_status"] = result["list_status"]
    result["st_raw_name"] = result["name"]
    result["last_updated_at"] = now
    return result


def normalize(dataset: str, raw: pd.DataFrame) -> pd.DataFrame:
    source = raw.copy()
    if dataset == "stk_mins":
        if "trade_time" not in source:
            raise ValueError("stk_mins.trade_time is required")
        parsed = pd.to_datetime(source["trade_time"].astype("string"), errors="raise")
        source["trade_date"] = parsed.dt.strftime("%Y%m%d")
        source["freq"] = source.get("freq", pd.Series("15min", index=source.index)).fillna("15min")
    if "security_id" in {f.name for f in SCHEMAS[dataset]}:
        source["security_id"] = source["ts_code"].map(lambda x: security_id(x) if pd.notna(x) else pd.NA)
    if dataset in FINANCIAL:
        order = [c for c in ("ann_date", "f_ann_date", "update_flag", "_fetched_at") if c in source]
        if order:
            source = source.sort_values(order, kind="stable", na_position="first")
    output = pd.DataFrame(index=source.index)
    for f in SCHEMAS[dataset]:
        values = source[f.source] if f.source in source else pd.Series(pd.NA, index=source.index)
        if f.dtype == "date":
            values = convert_dates(values)
        elif f.dtype == "datetime":
            values = pd.to_datetime(values, utc=True, errors="raise")
        elif f.dtype == "string":
            values = values.astype("string").replace("", pd.NA)
        else:
            values = pd.to_numeric(values, errors="raise").astype("Int64" if f.dtype == "int64" else "Float64")
        output[f.name] = values
    output["schema_version"] = schema_version(dataset)
    return output.drop_duplicates(KEYS[dataset], keep="last").reset_index(drop=True)


def arrow_schema(dataset: str) -> pa.Schema:
    types = {"string": pa.string(), "date": pa.date32(), "datetime": pa.timestamp("us", tz="UTC"),
             "float64": pa.float64(), "int64": pa.int64()}
    return pa.schema([(f.name, types[f.dtype]) for f in SCHEMAS[dataset]] + [("schema_version", pa.string())])


def expected_pool(master: pd.DataFrame, day: date, suspended: set[str] | None = None,
                  open_exchanges: set[str] | None = None) -> set[str]:
    # Coverage covers all listed A shares, including boards beyond the Task 02 pool.
    live = (master["list_date"].notna() & (master["list_date"] <= day)
            & (master["delist_date"].isna() | (master["delist_date"] > day)))
    if open_exchanges is not None:
        live &= (master["exchange"].isin(open_exchanges)
                 | (~master["exchange"].isin(["SSE", "SZSE"]) & bool(open_exchanges)))
    return set(master.loc[live, "ts_code"]) - (suspended or set())


def intraday_mainboard_codes(master: pd.DataFrame, day: date,
                             suspended: set[str] | None = None,
                             open_exchanges: set[str] | None = None) -> set[str]:
    """Main-board A shares eligible for a particular session, from official metadata.

    Include previously listed/delisted main-board shares only if they were live
    on that historical date. Never infer board membership from ticker prefixes.
    """
    eligible = expected_pool(master, day, suspended, open_exchanges)
    mainboard = master.loc[
        master["exchange"].isin(["SSE", "SZSE"])
        & master["board"].eq("MAIN")
        & master["security_type"].eq("A_SHARE"),
        "ts_code",
    ]
    return eligible & set(mainboard)


def mainboard_pool(master: pd.DataFrame) -> pd.DataFrame:
    return master.loc[(master["exchange"].isin(["SSE", "SZSE"])) & (master["board"] == "MAIN")
                      & (master["security_type"] == "A_SHARE") & (master["listing_status"] == "L")].copy()
