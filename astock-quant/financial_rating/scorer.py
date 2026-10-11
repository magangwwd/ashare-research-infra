"""
评分引擎：5个维度合计100分，罚分可使总分低于各维度之和，结果钳制到 [0, 100]。

权重（基于实证 Spearman 相关，2026-04-29 校准）：
  成长性 45 | 盈利能力 20 | 财务稳健 20 | 诚信治理 10 | 股东回报 5
  估值维度已移除（A股高PB=质量溢价，方向与价值投资假设相反，ρ=-0.220）
  稳健维度子项：资产负债率(11) + 流动比率(3) + 商誉(4) + 股东人数百分位(2) = 20
    股东人数百分位 = 跨截面绝对股东人数排名，代理市场关注度（高关注→正向信号）
"""

import os
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .config import (
    DATA_DIR, STOCK_LIST_CSV, INDUSTRY_CSV, DAILY_DATA_DIR,
    FINANCE_INDUSTRIES, SCORE_MAX, RATING_THRESHOLDS, RATING_PERCENTILES,
    CYCLE_DISCOUNT, CYCLE_THRESHOLD, YEARS,
)


# ── 辅助函数 ──────────────────────────────────────────────────────────

def _load_csv(filename: str) -> pd.DataFrame:
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return pd.DataFrame()

    for enc in ("utf-8-sig", "utf-8"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            pass
        except Exception:
            return pd.DataFrame()

    try:
        return pd.read_csv(path, encoding="utf-8", encoding_errors="replace")
    except Exception:
        pass

    try:
        return pd.read_csv(path, encoding="gb18030", encoding_errors="replace")
    except Exception:
        return pd.DataFrame()


def _safe_float(val) -> float | None:
    try:
        v = float(val)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _normalize_date(val) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return ""
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _cagr(start_val: float, end_val: float, periods: int) -> float | None:
    if periods <= 0 or start_val is None or end_val is None:
        return None
    if start_val <= 0:
        return None
    try:
        return (end_val / start_val) ** (1.0 / periods) - 1.0
    except Exception:
        return None


def _get_last_close(ts_code: str) -> float | None:
    path = os.path.join(DAILY_DATA_DIR, f"{ts_code}.csv")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 400))
            tail = f.read().decode("utf-8", errors="replace")
        lines = [l for l in tail.splitlines() if l.strip()]
        last = lines[-1].split(",")
        return _safe_float(last[1])  # Close is column index 1
    except Exception:
        return None


def _score_bracket(val: float | None, brackets: list[tuple]) -> float:
    """brackets = [(threshold_ge, score), ...] 从高到低排列。"""
    if val is None:
        return 0.0
    for threshold, score in brackets:
        if val >= threshold:
            return float(score)
    return 0.0


# ── 行业景气（方向2）─────────────────────────────────────────────────

def _calc_declining_industries(meta: pd.DataFrame, industry_map: dict) -> set[str]:
    """
    计算6M涨跌幅中位数 < CYCLE_THRESHOLD 的行业集合（≥5只有数据才纳入）。
    用于对景气下行行业的股票施加 CYCLE_DISCOUNT 折扣。
    """
    cutoff = (datetime.today() - timedelta(days=180)).strftime("%Y-%m-%d")
    ind_returns: dict[str, list[float]] = {}
    for _, row in meta.iterrows():
        code = row["ts_code"]
        ind  = industry_map.get(code, "")
        if not ind:
            continue
        path = os.path.join(DAILY_DATA_DIR, f"{code}.csv")
        if not os.path.exists(path):
            continue
        try:
            tmp = pd.read_csv(path, usecols=["Date", "Close"],
                              dtype={"Date": str, "Close": float})
            tmp = tmp[tmp["Date"] >= cutoff].sort_values("Date")
            if len(tmp) < 2:
                continue
            sp, ep = tmp["Close"].iloc[0], tmp["Close"].iloc[-1]
            if sp > 0:
                ind_returns.setdefault(ind, []).append((ep - sp) / sp)
        except Exception:
            continue
    return {ind for ind, rets in ind_returns.items()
            if len(rets) >= 5 and float(pd.Series(rets).median()) < CYCLE_THRESHOLD}


# ── 数据加载与聚合 ────────────────────────────────────────────────────

def _build_yearly(df: pd.DataFrame, value_cols: list[str], years: int) -> dict:
    if df.empty or "ts_code" not in df.columns or "end_date" not in df.columns:
        return {}
    df = df.copy()
    df["end_date"] = df["end_date"].apply(_normalize_date)
    df = df[df["end_date"].str.endswith("1231")].copy()
    df = df.drop_duplicates(subset=["ts_code", "end_date"])
    df = df.sort_values("end_date", ascending=False)

    result = {}
    for code, grp in df.groupby("ts_code"):
        grp = grp.head(years)
        result[code] = {col: [_safe_float(v) for v in grp[col].tolist()]
                        for col in value_cols if col in grp.columns}
    return result


def _avg(vals: list) -> float | None:
    clean = [v for v in vals if v is not None]
    return sum(clean) / len(clean) if clean else None


# ── 1. 盈利能力（20分） ───────────────────────────────────────────────
# 子项分配：ROE趋势(5) + OCF/净利润比(7) + 净利润率(6) + PB质量(2) = 20
# PB质量：A股高PB=质量溢价，行业内PB百分位高→护城河信号（ρ=+0.278@1Y）

def score_profitability(fi_data: dict, cf_data: dict, inc_data: dict,
                        pb_pct: float | None = None) -> tuple[float, dict]:
    # ROE 趋势：最新年 vs 前2年均值的 delta
    roe_vals = fi_data.get("roe", [])
    roe_avg = _avg(roe_vals)
    roe_recent = roe_vals[0] if roe_vals else None
    roe_hist = _avg(roe_vals[1:]) if len(roe_vals) > 1 else None

    roe_delta = None
    if roe_recent is not None and roe_hist is not None:
        roe_delta = roe_recent - roe_hist
    roe_score = _score_bracket(roe_delta, [(3, 5), (0, 3), (-3, 1), (-999, 0)])
    # 稳定高盈利（ROE绝对值≥15%）保底2分，防止高质量稳定股被过度惩罚
    if roe_recent is not None and roe_recent >= 15 and roe_score < 2:
        roe_score = 2.0

    # OCF / 净利润比（满分7，实证现金含量是盈利质量关键指标）
    inc_vals = inc_data.get("n_income_attr_p", [])
    cf_vals  = cf_data.get("n_cashflow_act", [])
    ratios = []
    for ocf, nip in zip(cf_vals, inc_vals):
        if ocf is not None and nip is not None and nip > 0:
            ratios.append(ocf / nip)
    ocf_ratio = _avg(ratios)
    ocf_score = _score_bracket(ocf_ratio, [(1.2, 7), (0.8, 5), (0.5, 2), (-999, 0)])

    # 净利润率（满分6）
    npm_vals = fi_data.get("netprofit_margin", [])
    npm_avg = _avg(npm_vals)
    npm_score = _score_bracket(npm_avg, [(20, 6), (10, 4), (5, 2), (-999, 0)])

    # PB质量信号（满分2）：行业内高PB = 市场认可的护城河，正向信号
    pb_score = 0.0
    if pb_pct is not None:
        if pb_pct >= 0.75:
            pb_score = 2.0
        elif pb_pct >= 0.50:
            pb_score = 1.0

    total = roe_score + ocf_score + npm_score + pb_score
    detail = {
        "roe_3yr_avg":   roe_avg,
        "roe_delta":     roe_delta,
        "ocf_ratio_avg": ocf_ratio,
        "npm_3yr_avg":   npm_avg,
    }
    return min(total, SCORE_MAX["profitability"]), detail


# ── 2. 成长性（45分） ─────────────────────────────────────────────────
# 子项：营收CAGR(20) + 净利CAGR(20) + 质量调整(-5~+5) = 合计上限45
# 质量调整：毛利率趋势 delta(±3) + 营收/净利一致性惩罚(-3)

def score_growth(inc_data: dict, fi_data: dict) -> tuple[float, dict]:
    rev_vals = inc_data.get("total_revenue", [])
    nip_vals = inc_data.get("n_income_attr_p", [])

    def _cagr_from_list(vals: list) -> float | None:
        clean = [v for v in vals if v is not None and v > 0]
        if len(clean) < 2:
            return None
        return _cagr(clean[-1], clean[0], len(clean) - 1)

    rev_cagr = _cagr_from_list(rev_vals)
    nip_cagr = _cagr_from_list(nip_vals)

    brackets = [
        (0.20, 20),   # CAGR ≥ 20%
        (0.10, 15),   # 10–20%
        (0.05,  9),   # 5–10%
        (0.00,  4),   # 0–5%
        (-0.05, 1),   # -5%~0%：轻度萎缩
        (-999,  0),   # < -5%
    ]
    rev_score = _score_bracket(rev_cagr, brackets)
    nip_score = _score_bracket(nip_cagr, brackets)

    # ── 质量调整 ──────────────────────────────────────────────────────
    quality_adj = 0.0

    # 毛利率趋势 delta（单位：百分点，如 30.5 表示 30.5%）
    gm_vals   = fi_data.get("grossprofit_margin", [])
    gm_delta  = None
    if len(gm_vals) >= 2:
        gm_r = gm_vals[0]
        gm_h = _avg(gm_vals[1:])
        if gm_r is not None and gm_h is not None:
            gm_delta = gm_r - gm_h
    if gm_delta is not None:
        if gm_delta > 2:      # 毛利率改善>2pp：护城河扩张
            quality_adj += 3
        elif gm_delta > 0:    # 小幅改善
            quality_adj += 1
        elif gm_delta < -3:   # 毛利率侵蚀>3pp：成本压力/竞争
            quality_adj -= 2

    # 一致性：净利增速>>营收增速（>15pp差距且营收低增），疑似非经营性收益
    if (rev_cagr is not None and nip_cagr is not None
            and nip_cagr - rev_cagr > 0.15 and rev_cagr < 0.05):
        quality_adj -= 3

    quality_adj = max(-5.0, min(5.0, quality_adj))

    total = rev_score + nip_score + quality_adj
    detail = {
        "revenue_cagr_3yr":   rev_cagr,
        "profit_cagr_3yr":    nip_cagr,
        "gm_delta":           round(gm_delta, 2) if gm_delta is not None else None,
        "growth_quality_adj": quality_adj,
    }
    return min(max(0.0, total), SCORE_MAX["growth"]), detail


# ── 3. 财务稳健（20分）────────────────────────────────────────────────
# 子项分配：资产负债率(11) + 流动比率(3) + 商誉(4) + 股东人数百分位(2) = 20
# 机构持股比例：ρ<0.05 不显著，保留为参考列（不参与评分）

# _INST_HOLDER_TYPES 保留用于 compute_scores 中计算 inst_ratio 参考列（不参与评分）
_INST_HOLDER_TYPES = frozenset({
    "开放式投资基金",
    "社保基金、社保机构",
    "金融机构—证券公司",
    "金融机构—银行",
    "保险投资组合",
    "资产管理公司",
    "基金专户理财",
    "基本养老保险基金",
    "金融机构—保险公司",
    "信托公司集合信托计划",
    "券商集合资产管理计划",
    "保险资管产品",
    "企业年金",
    "信托公司单一证券信托",
    "金融机构—信托公司",
    "其他金融产品",
})


def score_stability(fi_data: dict, goodwill_ratio: float | None,
                    industry: str,
                    hn_pct: float | None = None) -> tuple[float, dict]:
    # 资产负债率（金融行业豁免）→ 满分 11
    dta_vals = fi_data.get("debt_to_assets", [])
    dta = dta_vals[0] if dta_vals else None
    if industry in FINANCE_INDUSTRIES:
        dta_score = 8.0  # 金融行业中等分
    else:
        dta_score = 0.0
        if dta is not None:
            if dta < 40:
                dta_score = 11
            elif dta < 60:
                dta_score = 7
            elif dta < 70:
                dta_score = 3
            else:
                dta_score = 0

    # 流动比率 → 满分 3（原5分，让出2分给关注度子项）
    cr_vals = fi_data.get("current_ratio", [])
    cr = cr_vals[0] if cr_vals else None
    cr_score = _score_bracket(cr, [(2.0, 3), (1.5, 2), (1.0, 1), (-999, 0)])

    # 商誉占比 → 满分 4
    if goodwill_ratio is None:
        gw_score = 2.0
    elif goodwill_ratio < 5:
        gw_score = 4
    elif goodwill_ratio < 15:
        gw_score = 2
    else:
        gw_score = 1

    # 股东人数跨截面百分位 → 满分 2（市场关注度代理）
    # 高绝对股东人数 = 更多散户/机构覆盖 = 流动性与关注度信号（ρ=+0.109@1Y，实证正向）
    attn_score = 0.0
    if hn_pct is not None:
        if hn_pct >= 0.80:
            attn_score = 2.0
        elif hn_pct >= 0.60:
            attn_score = 1.0

    total = dta_score + cr_score + gw_score + attn_score
    detail = {
        "debt_to_assets":   dta,
        "current_ratio":    cr,
        "goodwill_ratio":   goodwill_ratio,
        "holder_count_pct": round(hn_pct, 3) if hn_pct is not None else None,
    }
    return min(total, SCORE_MAX["stability"]), detail


# ── 4. 股东回报（10分，有加分）────────────────────────────────────────
# 实证负相关（ρ=-0.119），大幅降权至10分；各子项减半

def score_shareholder(div_rows: list[dict], ts_code: str,
                       repurchase_rows: list[dict]) -> tuple[float, dict]:
    years_with_cash = set()
    has_stock_bonus = False
    latest_cash_div = 0.0

    for row in div_rows:
        ex_date = _normalize_date(row.get("ex_date", ""))
        if len(ex_date) >= 4:
            years_with_cash.add(ex_date[:4])
        stk = _safe_float(row.get("stk_div")) or 0
        bo  = _safe_float(row.get("stk_bo_rate")) or 0
        if stk > 0 or bo > 0:
            has_stock_bonus = True
        cash = _safe_float(row.get("cash_div_tax")) or 0
        latest_cash_div = max(latest_cash_div, cash)

    div_years = len(years_with_cash)
    div_years_score = {3: 5, 2: 3, 1: 1, 0: 0}.get(min(div_years, 3), 0)

    last_close = _get_last_close(ts_code)
    div_yield = None
    yield_score = 0
    if last_close and last_close > 0 and latest_cash_div > 0:
        div_yield = latest_cash_div / last_close
        yield_score = _score_bracket(div_yield, [(0.03, 3), (0.01, 1), (0.0001, 0.5), (-999, 0)])

    bonus_score = 1 if has_stock_bonus else 0
    repurchase_score = 1 if repurchase_rows else 0

    raw = div_years_score + yield_score + bonus_score + repurchase_score
    detail = {
        "dividend_years":   div_years,
        "dividend_yield":   div_yield,
        "has_stock_bonus":  has_stock_bonus,
        "has_repurchase":   bool(repurchase_rows),
        "_raw_shareholder": raw,
    }
    return raw, detail


# ── 5. 诚信治理（10分，含罚分） ──────────────────────────────────────

def classify_audit_result(result: str) -> tuple[bool, bool, bool]:
    """Return (has_adverse, has_reserve, is_clean) for an audit result.

    "标准无保留意见" contains the substring "保留意见", so clean opinions must
    be detected before reserve opinions.  A true reserve opinion is one that
    says "保留意见" without saying "无保留".
    """
    text = str(result or "").strip()
    if not text:
        return False, False, False

    has_adverse = any(k in text for k in ("否定", "无法表示"))
    is_clean = "无保留" in text or "标准" in text
    has_reserve = ("保留意见" in text) and ("无保留" not in text)
    return has_adverse, has_reserve, is_clean

def score_integrity(audit_rows: list[dict], is_st: bool) -> tuple[float, float, dict]:
    """返回 (base_score, penalty, detail)"""
    penalty = 0.0
    base = 0.0

    has_adverse = False
    has_reserve = False
    clean_count = 0

    for row in audit_rows:
        adverse, reserve, clean = classify_audit_result(row.get("audit_result", ""))
        if adverse:
            has_adverse = True
        elif reserve:
            has_reserve = True
        elif clean:
            clean_count += 1

    if has_adverse:
        base = 0
        penalty += 15
    elif has_reserve:
        base = 5
    elif clean_count > 0:
        base = 10
    else:
        base = 5

    if is_st:
        penalty += 20

    detail = {
        "audit_has_adverse": has_adverse,
        "audit_has_reserve": has_reserve,
        "is_st": is_st,
    }
    return base, penalty, detail



# ── 主评分函数 ────────────────────────────────────────────────────────

def compute_scores(ts_codes: list[str] | None = None, years: int = YEARS) -> pd.DataFrame:
    print("加载存档数据...")

    meta = pd.read_csv(STOCK_LIST_CSV, encoding="utf-8-sig")
    if ts_codes:
        meta = meta[meta["ts_code"].isin(ts_codes)]

    try:
        industry_df = pd.read_csv(INDUSTRY_CSV, encoding="utf-8-sig")
        industry_map = dict(zip(industry_df["ts_code"], industry_df["industry"]))
    except Exception:
        industry_map = {}

    rf_path = os.path.join(DATA_DIR, "risk_flags.csv")
    if os.path.exists(rf_path):
        st_codes = set(pd.read_csv(rf_path, encoding="utf-8-sig")["ts_code"].dropna())
    else:
        st_codes = set()

    inc_raw  = _load_csv("income.csv")
    fi_raw   = _load_csv("fina_indicator.csv")
    cf_raw   = _load_csv("cashflow_detail.csv")
    div_raw  = _load_csv("dividend.csv")
    aud_raw  = _load_csv("audit.csv")
    rep_raw  = _load_csv("repurchase.csv")
    val_raw  = _load_csv("valuation.csv")
    hn_raw   = _load_csv("holder_number.csv")
    fh_raw   = _load_csv("top_floatholders.csv")
    hk_raw   = _load_csv("hk_hold.csv")

    inc_by_code = _build_yearly(inc_raw,  ["total_revenue", "n_income_attr_p"], years)
    fi_by_code  = _build_yearly(fi_raw,   ["roe", "debt_to_assets", "current_ratio",
                                            "netprofit_margin", "ocfps",
                                            "grossprofit_margin"], years)
    cf_by_code  = _build_yearly(cf_raw,   ["n_cashflow_act"], years)

    # 用最新季报覆盖当期单点指标（ROE/资产负债率/流动比率/净利润率）
    # CAGR/趋势计算仍用年报，grossprofit_margin 不覆盖（季报与年报横比会引入噪音）
    _Q_OVERRIDE_COLS = ("roe", "debt_to_assets", "current_ratio", "netprofit_margin")
    fi_q_raw = _load_csv("fina_indicator_q.csv")
    if not fi_q_raw.empty and "ts_code" in fi_q_raw.columns:
        valid_q = fi_q_raw[fi_q_raw["end_date"].astype(str) != "NA"]
        q_latest_period = None
        for _, qrow in valid_q.iterrows():
            c = str(qrow["ts_code"])
            if c not in fi_by_code:
                continue
            for col in _Q_OVERRIDE_COLS:
                q_val = _safe_float(qrow.get(col))
                if q_val is not None and fi_by_code[c].get(col):
                    fi_by_code[c][col][0] = q_val
            if q_latest_period is None:
                q_latest_period = str(qrow.get("end_date", ""))
        if q_latest_period:
            print(f"  季报覆盖：已加载 fina_indicator_q.csv，最新期参考 {q_latest_period}")

    # 绝对股东人数跨截面百分位（关注度代理）
    # 取各股最新一期绝对股东人数，全市场 rank(pct=True) → 高关注度正向加分
    hn_abs_map: dict[str, float] = {}
    if not hn_raw.empty and "ts_code" in hn_raw.columns:
        valid_hn = hn_raw[hn_raw["end_date"].astype(str) != "NA"].copy()
        valid_hn = valid_hn.sort_values("end_date", ascending=False)
        valid_hn = valid_hn.drop_duplicates(subset=["ts_code", "end_date"])
        for code, grp in valid_hn.groupby("ts_code"):
            nums = grp["holder_num"].dropna().tolist()
            if nums and nums[0] is not None and float(nums[0]) > 0:
                hn_abs_map[code] = float(nums[0])

    hn_pct_map: dict[str, float] = {}
    if hn_abs_map:
        counts = pd.Series(hn_abs_map)
        hn_pct_map = counts.rank(pct=True).to_dict()

    # 前十大流通股东机构持股比例 {ts_code: inst_pct}
    fh_map: dict[str, float] = {}
    if not fh_raw.empty and "ts_code" in fh_raw.columns:
        valid_fh = fh_raw[fh_raw["end_date"].astype(str) != "NA"].copy()
        valid_fh = valid_fh.sort_values("end_date", ascending=False)
        for code, grp in valid_fh.groupby("ts_code"):
            latest = grp["end_date"].iloc[0]
            rows = grp[grp["end_date"] == latest]
            inst_mask = rows["holder_type"].isin(_INST_HOLDER_TYPES)
            ratio = _safe_float(rows[inst_mask]["hold_ratio"].sum()) or 0.0
            fh_map[code] = ratio

    # 北向持股比例 {ts_code: ratio}
    hk_map: dict[str, float] = {}
    if not hk_raw.empty and "ts_code" in hk_raw.columns:
        for _, row in hk_raw.iterrows():
            r = _safe_float(row.get("ratio"))
            if r is not None:
                hk_map[str(row["ts_code"])] = r

    # 估值数据（pe_ttm/pb 用于参考列 + PB质量评分）
    val_map: dict[str, dict] = {}
    if not val_raw.empty and "ts_code" in val_raw.columns:
        for _, vrow in val_raw.iterrows():
            c = str(vrow["ts_code"])
            if c == "nan" or str(vrow.get("trade_date", "")) == "NA":
                continue
            val_map[c] = {k: _safe_float(vrow.get(k))
                          for k in ("pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv")}

    # PB行业内百分位预计算（用于方向3：PB质量信号）
    pb_pct_map: dict[str, float] = {}
    if val_map:
        tmp_rows = [{"ts_code": c, "industry": industry_map.get(c, ""), "pb": v.get("pb")}
                    for c, v in val_map.items()]
        tmp = pd.DataFrame(tmp_rows)
        for ind, grp in tmp.groupby("industry"):
            pb_series = grp["pb"].dropna()
            if len(pb_series) < 5:
                continue
            for _, gr in grp.iterrows():
                pb = _safe_float(gr.get("pb"))
                if pb is not None:
                    pb_pct_map[gr["ts_code"]] = float((pb_series < pb).sum()) / len(pb_series)

    # 行业景气折扣（方向2）：计算6M中位收益<CYCLE_THRESHOLD的行业集合
    print("计算行业景气折扣（读日线数据）...")
    declining_inds = _calc_declining_industries(meta, industry_map)
    print(f"  景气折扣行业：{len(declining_inds)} 个")

    def _rows_for(df: pd.DataFrame, ts_code: str) -> list[dict]:
        if df.empty or "ts_code" not in df.columns:
            return []
        return df[df["ts_code"] == ts_code].to_dict("records")

    results = []
    total = len(meta)
    print(f"开始评分，共 {total} 只股票...")

    for i, row in meta.iterrows():
        code      = row["ts_code"]
        name      = row.get("name", "")
        gw_ratio  = _safe_float(row.get("goodwill_ratio"))
        industry  = industry_map.get(code, "")
        is_st     = code in st_codes

        inc_d = inc_by_code.get(code, {})
        fi_d  = fi_by_code.get(code, {})
        cf_d  = cf_by_code.get(code, {})
        div_r = _rows_for(div_raw, code)
        aud_r = _rows_for(aud_raw, code)
        rep_r = _rows_for(rep_raw, code)

        # 参考列
        inst_ratio = (fh_map.get(code) or 0.0) + (hk_map.get(code) or 0.0)
        val_info   = val_map.get(code, {})
        pb_pct     = pb_pct_map.get(code)
        hn_pct     = hn_pct_map.get(code)

        p_score, p_det = score_profitability(fi_d, cf_d, inc_d, pb_pct=pb_pct)
        g_score, g_det = score_growth(inc_d, fi_d)
        s_score, s_det = score_stability(fi_d, gw_ratio, industry, hn_pct=hn_pct)
        sh_raw, sh_det = score_shareholder(div_r, code, rep_r)
        i_base, i_pen, i_det = score_integrity(aud_r, is_st)

        sh_capped = min(sh_raw, SCORE_MAX["shareholder"])
        raw_total = p_score + g_score + s_score + sh_capped + i_base
        pre_score = max(0.0, min(100.0, raw_total - i_pen))

        # 方向2：行业景气折扣（折扣后再钳制）
        in_decline = industry in declining_inds
        total_score = round(pre_score * CYCLE_DISCOUNT, 1) if in_decline else round(pre_score, 1)

        audit_latest = (aud_r[0].get("audit_result", "") if aud_r else "")

        record = {
            "ts_code":             code,
            "name":                name,
            "industry":            industry,
            "total_score":         total_score,
            "rating":              "",          # 二次遍历填入（动态分位）
            "profitability_score": round(p_score, 1),
            "growth_score":        round(g_score, 1),
            "stability_score":     round(s_score, 1),
            "shareholder_score":   round(sh_capped, 1),
            "integrity_score":     round(max(0, i_base - i_pen), 1),
            **{k: (round(v, 4) if isinstance(v, float) else v)
               for k, v in {**p_det, **g_det, **s_det, **sh_det, **i_det}.items()
               if not k.startswith("_")},
            # 参考列（不参与评分）
            "pe_ttm":       val_info.get("pe_ttm"),
            "pb":           val_info.get("pb"),
            "inst_ratio":   round(inst_ratio, 2) if inst_ratio else None,
            "cycle_discount": in_decline,
            "audit_latest": audit_latest,
        }
        results.append(record)

        if (i + 1) % 100 == 0 or (i + 1) == total:
            print(f"  已评分：{i + 1}/{total}")

    # ── 方向4：动态分位评级 ──────────────────────────────────────────────
    scores_arr = np.array([r["total_score"] for r in results])
    if len(scores_arr) >= 50:
        # 样本充足：用百分位动态阈值（RATING_PERCENTILES 从高到低）
        actual_thresholds = sorted(
            [(float(np.percentile(scores_arr, pct)), label)
             for pct, label in RATING_PERCENTILES],
            reverse=True,
        )
        for r in results:
            r["rating"] = next(
                label for (thresh, label) in actual_thresholds
                if r["total_score"] >= thresh
            )
        print(f"  动态评级阈值（实际得分）：" +
              "  ".join(f"{lb}≥{th:.1f}" for th, lb in actual_thresholds))
    else:
        # 样本不足（--test 模式）：回退到固定阈值
        for r in results:
            r["rating"] = next(label for (t, label) in RATING_THRESHOLDS
                               if r["total_score"] >= t)

    df_scores = pd.DataFrame(results).sort_values("total_score", ascending=False)
    df_scores = df_scores.reset_index(drop=True)

    out_path = os.path.join(DATA_DIR, "scores.csv")
    df_scores.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"评分完成，结果已保存至 {out_path}")
    return df_scores
