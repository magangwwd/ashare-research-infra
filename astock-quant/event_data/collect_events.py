from __future__ import annotations

import argparse
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "event_data"
STOCK_LIST_CSV = BASE_DIR / "filtered_stocks.csv"

ANNOUNCEMENTS_CSV = DATA_DIR / "announcements.csv"
RESEARCH_CSV = DATA_DIR / "research_reports.csv"
NEWS_CSV = DATA_DIR / "stock_news.csv"
ERRORS_CSV = DATA_DIR / "collect_errors.csv"


def _normalize_ts_date(value: str | None) -> pd.Timestamp:
    if value:
        return pd.Timestamp(value)
    return pd.Timestamp.today().normalize()


def _yyyymmdd(value: pd.Timestamp) -> str:
    return pd.Timestamp(value).strftime("%Y%m%d")


def _symbol(ts_code: str) -> str:
    return str(ts_code).split(".")[0].zfill(6)


def _load_codes(codes_arg: str | None, sample_size: int | None, sample_seed: int) -> pd.DataFrame:
    meta = pd.read_csv(STOCK_LIST_CSV, encoding="utf-8-sig")
    meta["ts_code"] = meta["ts_code"].astype(str)
    if codes_arg:
        wanted = {code.strip() for code in codes_arg.split(",") if code.strip()}
        meta = meta[meta["ts_code"].isin(wanted)].copy()
    else:
        meta = meta.sort_values("ts_code").reset_index(drop=True)
        if sample_size is not None:
            meta = meta.sample(n=min(sample_size, len(meta)), random_state=sample_seed)
    return meta[["ts_code", "name"]].drop_duplicates("ts_code").reset_index(drop=True)


def _append_dedupe(path: Path, rows: pd.DataFrame, key_cols: list[str]) -> pd.DataFrame:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    if path.exists():
        frames.append(pd.read_csv(path, dtype=str))
    if rows is not None and not rows.empty:
        frames.append(rows.astype(str))
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    existing_keys = [col for col in key_cols if col in merged.columns]
    if existing_keys:
        merged = merged.drop_duplicates(existing_keys, keep="last")
    merged = merged.sort_values([col for col in ["ts_code", "event_date", "publish_time", "title"] if col in merged.columns])
    merged.to_csv(path, index=False, encoding="utf-8-sig")
    return merged


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _col(df: pd.DataFrame, name: str, default: str = "") -> pd.Series:
    if name in df.columns:
        return df[name]
    return pd.Series(default, index=df.index)


RISK_KEYWORDS = (
    "减持",
    "诉讼",
    "仲裁",
    "立案",
    "处罚",
    "问询",
    "退市",
    "风险",
    "监管",
    "违规",
    "亏损",
    "预亏",
    "质押",
    "担保",
    "冻结",
    "终止",
)

POSITIVE_KEYWORDS = (
    "回购",
    "增持",
    "中标",
    "合同",
    "分红",
    "权益分派",
    "预增",
    "扭亏",
    "业绩增长",
)


def _normalize_title_flags(df: pd.DataFrame, title_col: str) -> pd.DataFrame:
    out = df.copy()
    text = out[title_col].fillna("").astype(str)
    out["is_risk"] = text.map(lambda value: int(_contains_any(value, RISK_KEYWORDS)))
    out["is_positive"] = text.map(lambda value: int(_contains_any(value, POSITIVE_KEYWORDS)))
    return out


def fetch_announcements(ak, ts_code: str, name: str, start: str, end: str, limit_per_code: int) -> pd.DataFrame:
    df = ak.stock_zh_a_disclosure_report_cninfo(
        symbol=_symbol(ts_code),
        market="沪深京",
        start_date=start,
        end_date=end,
    )
    if df is None or df.empty:
        return pd.DataFrame()
    event_date = pd.to_datetime(_col(df, "公告时间"), errors="coerce")
    out = pd.DataFrame(
        {
            "ts_code": ts_code,
            "name": name,
            "event_date": event_date.dt.strftime("%Y-%m-%d"),
            "title": _col(df, "公告标题").astype(str),
            "url": _col(df, "公告链接").astype(str),
            "source": "cninfo",
        }
    ).dropna(subset=["event_date"])
    out = _normalize_title_flags(out, "title")
    return out.head(limit_per_code)


def fetch_research(ak, ts_code: str, name: str, start_ts: pd.Timestamp, end_ts: pd.Timestamp, limit_per_code: int) -> pd.DataFrame:
    df = ak.stock_research_report_em(symbol=_symbol(ts_code))
    if df is None or df.empty:
        return pd.DataFrame()
    report_date = pd.to_datetime(_col(df, "日期"), errors="coerce")
    mask = (report_date >= start_ts) & (report_date <= end_ts)
    df = df.loc[mask].copy()
    if df.empty:
        return pd.DataFrame()
    rating = _col(df, "东财评级").fillna("").astype(str)
    out = pd.DataFrame(
        {
            "ts_code": ts_code,
            "name": name,
            "event_date": report_date.loc[df.index].dt.strftime("%Y-%m-%d"),
            "title": _col(df, "报告名称").astype(str),
            "rating": rating,
            "institution": _col(df, "机构").astype(str),
            "industry": _col(df, "行业").astype(str),
            "pdf_url": _col(df, "报告PDF链接").astype(str),
            "source": "eastmoney_research",
        }
    ).dropna(subset=["event_date"])
    out["is_buy_rating"] = out["rating"].str.contains("买入|增持|推荐|强烈推荐").astype(int)
    return out.head(limit_per_code)


def fetch_news(ak, ts_code: str, name: str, start_ts: pd.Timestamp, end_ts: pd.Timestamp, limit_per_code: int) -> pd.DataFrame:
    df = ak.stock_news_em(symbol=_symbol(ts_code))
    if df is None or df.empty:
        return pd.DataFrame()
    publish_time = pd.to_datetime(_col(df, "发布时间"), errors="coerce")
    mask = (publish_time >= start_ts) & (publish_time <= end_ts + pd.Timedelta(days=1))
    df = df.loc[mask].copy()
    if df.empty:
        return pd.DataFrame()
    publish_time = publish_time.loc[df.index]
    out = pd.DataFrame(
        {
            "ts_code": ts_code,
            "name": name,
            "event_date": publish_time.dt.strftime("%Y-%m-%d"),
            "publish_time": publish_time.dt.strftime("%Y-%m-%d %H:%M:%S"),
            "title": _col(df, "新闻标题").astype(str),
            "summary": _col(df, "新闻内容").astype(str),
            "publisher": _col(df, "文章来源").astype(str),
            "url": _col(df, "新闻链接").astype(str),
            "source": "eastmoney_news",
        }
    ).dropna(subset=["event_date"])
    out = _normalize_title_flags(out, "title")
    return out.head(limit_per_code)


def collect_events(args: argparse.Namespace) -> None:
    import akshare as ak

    end_ts = _normalize_ts_date(args.end_date)
    start_ts = _normalize_ts_date(args.start_date) if args.start_date else end_ts - timedelta(days=args.days)
    start = _yyyymmdd(start_ts)
    end = _yyyymmdd(end_ts)
    codes = _load_codes(args.codes, args.sample_size, args.sample_seed)
    print(f"[events] codes={len(codes)}, range={start}~{end}", flush=True)

    ann_frames: list[pd.DataFrame] = []
    research_frames: list[pd.DataFrame] = []
    news_frames: list[pd.DataFrame] = []
    errors: list[dict[str, str]] = []

    for i, row in enumerate(codes.itertuples(index=False), start=1):
        ts_code = str(row.ts_code)
        name = str(row.name)
        print(f"[events] {i}/{len(codes)} {ts_code} {name}", flush=True)
        jobs = []
        if not args.skip_announcements:
            jobs.append(("announcements", lambda: fetch_announcements(ak, ts_code, name, start, end, args.limit_per_code)))
        if not args.skip_research:
            jobs.append(("research", lambda: fetch_research(ak, ts_code, name, start_ts, end_ts, args.limit_per_code)))
        if not args.skip_news:
            jobs.append(("news", lambda: fetch_news(ak, ts_code, name, start_ts, end_ts, args.limit_per_code)))

        for kind, fn in jobs:
            try:
                data = fn()
                if kind == "announcements" and not data.empty:
                    ann_frames.append(data)
                elif kind == "research" and not data.empty:
                    research_frames.append(data)
                elif kind == "news" and not data.empty:
                    news_frames.append(data)
                print(f"  {kind}: {len(data)}", flush=True)
            except Exception as exc:
                errors.append({"ts_code": ts_code, "name": name, "kind": kind, "error": f"{type(exc).__name__}: {exc}"})
                print(f"  {kind}: error {type(exc).__name__}: {exc}", flush=True)
            time.sleep(args.sleep)

    if ann_frames:
        merged = _append_dedupe(ANNOUNCEMENTS_CSV, pd.concat(ann_frames, ignore_index=True), ["ts_code", "event_date", "title", "url"])
        print(f"[events] announcements saved rows={len(merged)} -> {ANNOUNCEMENTS_CSV}", flush=True)
    if research_frames:
        merged = _append_dedupe(RESEARCH_CSV, pd.concat(research_frames, ignore_index=True), ["ts_code", "event_date", "title", "institution"])
        print(f"[events] research saved rows={len(merged)} -> {RESEARCH_CSV}", flush=True)
    if news_frames:
        merged = _append_dedupe(NEWS_CSV, pd.concat(news_frames, ignore_index=True), ["ts_code", "publish_time", "title", "url"])
        print(f"[events] news saved rows={len(merged)} -> {NEWS_CSV}", flush=True)
    if errors:
        _append_dedupe(ERRORS_CSV, pd.DataFrame(errors), ["ts_code", "kind", "error"])
        print(f"[events] errors={len(errors)} -> {ERRORS_CSV}", flush=True)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="采集公告、研报和个股新闻元数据")
    parser.add_argument("--codes", type=str, default=None, help="逗号分隔 ts_code；默认从股票池抽样或全量")
    parser.add_argument("--sample-size", type=int, default=20, help="未指定 codes 时抽样股票数；设为 0 表示全量")
    parser.add_argument("--sample-seed", type=int, default=42)
    parser.add_argument("--start-date", type=str, default=None, help="YYYY-MM-DD；默认 end-date 往前 days")
    parser.add_argument("--end-date", type=str, default=None, help="YYYY-MM-DD；默认今天")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit-per-code", type=int, default=100)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--skip-announcements", action="store_true")
    parser.add_argument("--skip-research", action="store_true")
    parser.add_argument("--skip-news", action="store_true")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    if args.sample_size == 0:
        args.sample_size = None
    collect_events(args)


if __name__ == "__main__":
    main()
