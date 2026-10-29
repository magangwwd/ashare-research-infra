"""Collect bounded official feeds and retain original bytes and explicit gaps."""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
from html.parser import HTMLParser
import json
import io
import math
import os
from pathlib import Path
import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

UA = "AshareResearchInfra/1.0 (+https://github.com/magangwwd/ashare-research-infra/issues/124)"
MAX_BYTES = 25_000_000
HOSTS = {"home.treasury.gov", "investor.tsmc.com", "investor.cld.tsmc.com", "ir.eia.gov", "data.sec.gov",
         "mopsfin.twse.com.tw", "www.ecb.europa.eu", "www.sse.net.cn"}
TWSE_COMPANIES = {"2330", "2317", "2382", "3231", "2356", "3037", "8046"}
COMPANIES = {"NVDA": "0001045810", "MU": "0000723125", "MSFT": "0000789019",
             "GOOGL": "0001652044", "META": "0001326801", "AMZN": "0001018724", "AAPL": "0000320193"}


def stamp(now):
    return now.astimezone(timezone.utc).isoformat()


def finite(value):
    if isinstance(value, bool):
        raise ValueError("boolean_is_not_numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non_finite_numeric")
    return result


def download(url, *, attempts=2):
    if urlparse(url).scheme != "https" or urlparse(url).hostname not in HOSTS:
        raise ValueError("unregistered_source")
    for attempt in range(attempts):
        try:
            with urlopen(Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"}), timeout=20) as response:
                # EIA currently redirects to its own signed HTTPS delivery URL.
                final = urlparse(response.geturl())
                if final.scheme != "https" or final.hostname not in HOSTS:
                    raise ValueError("unregistered_redirect")
                raw = response.read(MAX_BYTES + 1)
                if not raw or len(raw) > MAX_BYTES:
                    raise ValueError("unexpected_response_size")
                return raw
        except HTTPError as exc:
            # Respect access denials. Never rotate identities or bypass a block.
            if exc.code not in (429, 500, 502, 503, 504) or attempt + 1 == attempts:
                raise
        except (TimeoutError, OSError):
            if attempt + 1 == attempts:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("download_attempts_exhausted")


class TableRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.cells, self.text, self.in_cell = [], [], [], False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cells = []
        if tag in ("td", "th"):
            self.in_cell, self.text = True, []

    def handle_data(self, data):
        if self.in_cell:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.in_cell:
            self.cells.append(" ".join("".join(self.text).split()))
            self.in_cell = False
        if tag == "tr" and self.cells:
            self.rows.append(self.cells)


def observation(metric, period, actual, unit, **extra):
    return {"metric": metric, "period": period, "actual": finite(actual), "unit": unit,
            "consensus": None, "published_at": None, "published_date": None,
            "publication_precision": "unknown", **extra}


def parse_tsmc(raw, now):
    html = raw.decode("utf-8-sig")
    if "Millions of New Taiwan Dollars" not in html:
        raise ValueError("tsmc_currency_or_unit_missing")
    parser = TableRows()
    parser.feed(html)
    year = now.astimezone(ZoneInfo("Asia/Taipei")).year
    if not re.search(rf"{year}\s+Monthly Revenue", html):
        raise ValueError("tsmc_year_missing")
    months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
              "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
    records = []
    for cells in parser.rows:
        month = months.get(cells[0].strip().lower()[:3])
        if not month or len(cells) < 3 or not cells[1].strip():
            continue
        value = finite(cells[1].replace(",", ""))
        end = date(year, month, calendar.monthrange(year, month)[1])
        if end >= now.astimezone(ZoneInfo("Asia/Taipei")).date() or value <= 0:
            raise ValueError("tsmc_future_or_invalid_period")
        yoy = finite(cells[2].replace("%", "").replace("(", "-").replace(")", ""))
        records.append(observation("tsmc_consolidated_monthly_revenue", end.isoformat(), value,
                                   "TWD_million", yoy_pct=yoy))
    if not records or len({r["period"] for r in records}) != len(records):
        raise ValueError("tsmc_missing_or_duplicate_months")
    return sorted(records, key=lambda r: r["period"])


def parse_treasury(raw, now):
    root = ET.fromstring(raw)
    records = []
    for entry in root.findall("{*}entry"):
        props = next(iter(entry.findall(".//{*}properties")), None)
        if props is None:
            continue
        values = {node.tag.split("}")[-1]: node.text for node in props}
        if not values.get("NEW_DATE") or not values.get("TC_10YEAR"):
            continue
        day = date.fromisoformat(values["NEW_DATE"][:10])
        if day > now.astimezone(ZoneInfo("America/New_York")).date():
            raise ValueError("treasury_future_date")
        value = finite(values["TC_10YEAR"])
        if not -10 <= value <= 30:
            raise ValueError("treasury_out_of_range")
        records.append(observation("us_treasury_10y_par_real_yield", day.isoformat(), value, "percent"))
    if not records or len({r["period"] for r in records}) != len(records):
        raise ValueError("treasury_missing_or_duplicate_rates")
    return sorted(records, key=lambda r: r["period"])[-15:]


def parse_twse_revenue(raw, now):
    """Official open dataset; native amounts are NT$ thousands, ROC calendar."""
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    required = {'出表日期', '資料年月', '公司代號', '公司名稱', '營業收入-當月營收',
                '營業收入-上月營收', '營業收入-去年當月營收', '營業收入-去年同月增減(%)'}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError('twse_missing_required_columns')
    records, seen = [], set()
    for row in reader:
        code = row['公司代號'].strip()
        if code not in TWSE_COMPANIES:
            continue
        month = row['資料年月'].strip()
        if not re.fullmatch(r'\d{5}', month):
            raise ValueError('twse_invalid_roc_period')
        year, mon = int(month[:3]) + 1911, int(month[3:])
        end = date(year, mon, calendar.monthrange(year, mon)[1])
        if end >= now.astimezone(ZoneInfo('Asia/Taipei')).date():
            raise ValueError('twse_future_or_incomplete_month')
        identity = (code, end)
        if identity in seen:
            raise ValueError('twse_duplicate_company_period')
        seen.add(identity)
        actual, prior, year_ago = (finite(row[k].replace(',', '')) for k in
                                  ('營業收入-當月營收', '營業收入-上月營收', '營業收入-去年當月營收'))
        if min(actual, prior, year_ago) <= 0:
            raise ValueError('twse_invalid_revenue')
        yoy = finite(row['營業收入-去年同月增減(%)'])
        if abs((actual / year_ago - 1) * 100 - yoy) > .02:
            raise ValueError('twse_yoy_arithmetic_mismatch')
        table_date = row['出表日期'].strip()
        if not re.fullmatch(r'\d{7}', table_date):
            raise ValueError('twse_invalid_table_date')
        made = date(int(table_date[:3]) + 1911, int(table_date[3:5]), int(table_date[5:]))
        if made > now.astimezone(ZoneInfo('Asia/Taipei')).date():
            raise ValueError('twse_future_table_date')
        records.append(observation('twse_monthly_revenue_' + code, end.isoformat(), actual, 'TWD_thousand',
            company=code, company_name=row['公司名稱'], exchange='TWSE', prior=prior, year_ago=year_ago,
            yoy_pct=yoy, mom_pct=(actual / prior - 1) * 100,
            dataset_table_date=made.isoformat(),
            publication_note='Dataset table date is not the original company release timestamp.'))
    if not records:
        raise ValueError('twse_target_companies_missing')
    return records


def parse_ecb_fx(raw, now):
    """Reference rates against EUR; explicitly labelled calculated cross rate."""
    root = ET.fromstring(raw)
    blocks = [node for node in root.iter() if node.attrib.get('time')]
    if len(blocks) != 1:
        raise ValueError('ecb_expected_one_reference_date')
    day = date.fromisoformat(blocks[0].attrib['time'])
    if day > now.astimezone(ZoneInfo('Europe/Berlin')).date():
        raise ValueError('ecb_future_reference_date')
    rates = {}
    for node in blocks[0].iter():
        code = node.attrib.get('currency')
        if code:
            if code in rates:
                raise ValueError('ecb_duplicate_currency')
            rates[code] = finite(node.attrib['rate'])
            if rates[code] <= 0:
                raise ValueError('ecb_nonpositive_reference_rate')
    if not {'USD', 'CNY', 'JPY'}.issubset(rates):
        raise ValueError('ecb_required_currency_missing')
    records = [observation('ecb_EUR_' + code + '_reference', day.isoformat(), rates[code], code + '_per_EUR',
                 quote_kind='central_bank_reference_not_executable_quote')
               for code in ('USD', 'CNY', 'JPY', 'AUD', 'HKD') if code in rates]
    records.append(observation('ecb_USD_CNY_reference_cross', day.isoformat(), rates['CNY'] / rates['USD'],
        'CNY_per_USD', derived=True, formula='CNY_per_EUR / USD_per_EUR',
        quote_kind='calculated_reference_cross_not_DXY_or_live_USDCNY'))
    return records


def parse_scfi(raw, now):
    parser = TableRows()
    parser.feed(raw.decode('utf-8-sig'))
    headers = [r for r in parser.rows if len(r) >= 6 and '本期' in r[4] and '上期' in r[3]]
    values = [r for r in parser.rows if len(r) >= 6 and 'Comprehensive Index' in r[0]]
    if len(headers) != 1 or len(values) != 1:
        raise ValueError('scfi_dated_composite_table_missing')
    match = [re.search(r'\d{4}-\d{2}-\d{2}', headers[0][i]) for i in (3, 4)]
    if not all(match):
        raise ValueError('scfi_reference_dates_missing')
    old, day = [date.fromisoformat(m.group()) for m in match]
    if old >= day or day > now.astimezone(ZoneInfo('Asia/Shanghai')).date():
        raise ValueError('scfi_invalid_date_sequence')
    prior, actual, change = [finite(values[0][i].replace(',', '')) for i in (3, 4, 5)]
    if min(prior, actual) <= 0 or abs(actual - prior - change) > .03:
        raise ValueError('scfi_composite_arithmetic_mismatch')
    return [observation('shanghai_export_container_spot_freight_composite_scfi', day.isoformat(), actual,
                        'index_points', prior=prior, prior_period=old.isoformat(), change=change,
                        index_type='SCFI_not_BDI_not_SCFIS',
                        coverage='composite_only_route_prices_not_collected')]


def parse_eia(raw, now):
    doc = json.loads(raw)
    meta = doc["metadata"]
    series = doc["data"]["U.S."]
    if series.get("sourcekey") != "WCESTUS1" or series.get("units") != "thousand barrels":
        raise ValueError("eia_wrong_series_or_unit")
    release_date = date.fromisoformat(meta["release_date"])
    release_time = datetime.strptime(meta["release_time"].strip().upper(), "%I:%M %p").time()
    released = datetime.combine(release_date, release_time, ZoneInfo("America/New_York"))
    if released > now:
        raise ValueError("eia_future_release")
    records = []
    for point in series["time_series"]:
        if point.get("suppression_flag") is not None or point.get("value") is None:
            continue
        day = date.fromisoformat(point["date"])
        value = finite(point["value"])
        if day > release_date or value <= 0:
            raise ValueError("eia_future_or_invalid_point")
        records.append(observation("us_commercial_crude_stocks_excluding_spr", day.isoformat(), value,
                                   "thousand_barrels"))
    records.sort(key=lambda r: r["period"])
    if len(records) < 2 or len({r["period"] for r in records}) != len(records):
        raise ValueError("eia_missing_or_duplicate_points")
    # This release timestamp belongs only to the latest observation, not the historical series.
    records[-1].update(published_at=stamp(released), published_date=release_date.isoformat(),
                       publication_precision="timestamp", prior=records[-2]["actual"],
                       change=records[-1]["actual"] - records[-2]["actual"])
    return records[-12:]


def parse_sec(submissions_raw, facts_raw, now, ticker, cik):
    submissions, facts = json.loads(submissions_raw), json.loads(facts_raw)
    if int(submissions["cik"]) != int(cik) or int(facts["cik"]) != int(cik):
        raise ValueError("sec_cik_mismatch")
    recent = submissions["filings"]["recent"]
    columns = ("accessionNumber", "filingDate", "form", "primaryDocument")
    size = len(recent["accessionNumber"])
    if any(len(recent[k]) != size for k in columns):
        raise ValueError("sec_column_length_mismatch")
    accepted = {}
    for i in range(size):
        timestamp = recent.get("acceptanceDateTime", [None] * size)[i]
        if timestamp:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                raise ValueError("sec_naive_acceptance_timestamp")
            accepted[recent["accessionNumber"][i]] = dt
    concepts = facts.get("facts", {}).get("us-gaap", {})
    tags = {
        "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
        "net_income": ("NetIncomeLoss",),
        "capex_cash_paid": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    }
    records = []
    for metric, choices in tags.items():
        candidates = []
        for tag in choices:
            points = concepts.get(tag, {}).get("units", {}).get("USD", [])
            for point in points:
                if point.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
                    continue
                if not all(point.get(k) for k in ("start", "end", "filed", "accn")):
                    continue
                start, end, filed = (date.fromisoformat(point[k]) for k in ("start", "end", "filed"))
                if end > now.date() or filed > now.date() or (now.date() - end).days > 450:
                    continue
                duration = (end - start).days + 1
                kind = "quarter" if 70 <= duration <= 110 else "year" if 330 <= duration <= 400 else "year_to_date"
                release = accepted.get(point["accn"])
                if release and release > now:
                    continue
                candidates.append(observation("sec_" + metric, end.isoformat(), point["val"], "USD",
                    company=ticker, cik=cik, taxonomy="us-gaap", tag=tag, start=start.isoformat(),
                    duration_days=duration, duration_kind=kind, accession=point["accn"], form=point["form"],
                    published_at=stamp(release) if release else None, published_date=filed.isoformat(),
                    publication_precision="timestamp" if release else "date_only"))
            if candidates:
                break
        # Retain restatements and distinct durations; never relabel YTD as quarterly revenue.
        unique = {(r["start"], r["period"], r["accession"], r["actual"]): r for r in candidates}
        records.extend(sorted(unique.values(), key=lambda r: (r["published_date"], r["period"]))[-12:])
    if not any(r["metric"] == "sec_revenue" for r in records):
        raise ValueError("sec_supported_revenue_missing")
    return records


def freshness(source_id, records, now):
    latest = max(date.fromisoformat(r["period"]) for r in records)
    if source_id in ("tsmc", "twse_monthly"):
        local = now.astimezone(ZoneInfo("Asia/Taipei")).date()
        first = local.replace(day=1)
        expected = first - timedelta(days=1)
        # A conservative monthly deadline; exact holiday-specific dates are not claimed.
        if local.day <= 10:
            expected = expected.replace(day=1) - timedelta(days=1)
        stale = latest < expected
        policy = "previous_month_after_day_10_conservative_not_exact_release_calendar"
    elif source_id in ("treasury", "ecb_fx"):
        stale = (now.date() - latest).days > 7
        policy = "7_calendar_day_ceiling_exact_us_holiday_calendar_not_integrated"
    elif source_id in ("eia", "scfi"):
        stale = (now.date() - latest).days > 14
        policy = "14_calendar_day_ceiling_exact_exception_calendar_not_integrated"
    else:
        # SEC is an event feed. An old filing is not a failed daily update.
        stale = False
        policy = "irregular_filings_current_api_fetch_no_expected_new_filing_assumed"
    return {"status": "stale" if stale else "accepted", "latest_period": latest.isoformat(),
            "freshness_policy": policy, "exact_release_calendar_verified": False}


def capture(records, source_id, now, previous):
    old = {r["event_id"]: r for r in previous.get("observations", [])}
    cutoff = previous.get("generated_at_utc")
    for record in records:
        identity = [source_id, record["metric"], record["period"], record.get("start"), record.get("accession")]
        record["event_id"] = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        prior = old.get(record["event_id"])
        unchanged = prior and prior["actual"] == record["actual"]
        record["source_id"] = source_id
        record["first_seen_at"] = prior["first_seen_at"] if unchanged else stamp(now)
        record["fetched_at"] = stamp(now)
        if unchanged:
            kind = "unchanged"
        elif prior:
            kind = "revision"
            record["previous_actual"] = prior["actual"]
        elif not cutoff:
            kind = "bootstrap"
        elif record["published_at"] and datetime.fromisoformat(record["published_at"]) > datetime.fromisoformat(cutoff):
            kind = "new_publication"
        else:
            kind = "late_capture_or_publication_time_unknown"
        record["capture_kind"] = kind
        record["eligible_as_new_event"] = kind == "new_publication"


def collect(output, now, fetch=download, previous=None, companies=None, extra_sources=False):
    if now.tzinfo is None:
        raise ValueError("timezone_required")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("output_must_be_new_or_empty")
    raw_dir = output / "raw"
    raw_dir.mkdir()
    previous = previous or {}
    sources, observations, raw_files = [], [], []

    def source(source_id, urls, parser):
        raw = []
        provenance = []
        try:
            for index, url in enumerate(urls):
                blob = fetch(url)
                name = f"raw/{source_id}_{index}.bin"
                (output / name).write_bytes(blob)
                item = {"path": name, "source_id": source_id, "source_url": url,
                        "sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob), "fetched_at": stamp(now)}
                provenance.append(item)
                raw_files.append(item)
                raw.append(blob)
            records = parser(*raw)
            state = freshness(source_id, records, now)
            state.update(source_id=source_id, source_urls=urls, raw_files=provenance,
                         observation_count=len(records), fetched_at=stamp(now))
            if source_id == 'twse_monthly':
                state['missing_companies'] = sorted(TWSE_COMPANIES - {r['company'] for r in records})
            if state["status"] == "accepted":
                capture(records, source_id, now, previous)
                for record in records:
                    record["source_urls"] = urls
                    record["raw_sha256"] = [p["sha256"] for p in provenance]
                observations.extend(records)
            sources.append(state)
        except Exception as exc:
            sources.append({"source_id": source_id, "source_urls": urls, "status": "missing_or_invalid",
                            "error_type": type(exc).__name__, "http_status": getattr(exc, "code", None),
                            "raw_files": provenance,
                            "observation_count": 0, "fetched_at": stamp(now)})

    year = now.astimezone(ZoneInfo("Asia/Taipei")).year
    source("tsmc", [f"https://investor.tsmc.com/english/monthly-revenue/{year}"], lambda raw: parse_tsmc(raw, now))
    treasury = f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_real_yield_curve&field_tdr_date_value={now.year}"
    source("treasury", [treasury], lambda raw: parse_treasury(raw, now))
    source("eia", ["https://ir.eia.gov/wpsr/psw00.json"], lambda raw: parse_eia(raw, now))
    if extra_sources:
        source('twse_monthly', ['https://mopsfin.twse.com.tw/opendata/t187ap05_L.csv'],
               lambda raw: parse_twse_revenue(raw, now))
        source('ecb_fx', ['https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'],
               lambda raw: parse_ecb_fx(raw, now))
        source('scfi', ['https://www.sse.net.cn/index/singleIndex?indexType=scfi'],
               lambda raw: parse_scfi(raw, now))
    for ticker, cik in (COMPANIES if companies is None else companies).items():
        source("sec_" + ticker.lower(), [f"https://data.sec.gov/submissions/CIK{cik}.json",
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"],
            lambda sub, facts, ticker=ticker, cik=cik: parse_sec(sub, facts, now, ticker, cik))
        if fetch is download:
            time.sleep(1)  # Sequential, far below SEC's maximum request rate.
    count = sum(s["status"] == "accepted" for s in sources)
    complete = count == len(sources) and not any(s.get('missing_companies') for s in sources)
    snapshot = {"schema": "overseas_observations_v1", "generated_at_utc": stamp(now),
        "mode": "OFFICIAL_DATA_ONLY_NO_LLM_NO_TRADING", "bootstrap": not bool(previous),
        "quality": "passed" if complete else "passed_with_gaps" if count else "blocked",
        "sources": sources, "observations": observations, "raw_files": raw_files,
        "limitations": ["Exact release calendars not yet integrated; see per-source conservative freshness policy.",
                         "No market consensus or capital-expenditure guidance is synthesized.",
                         "Bootstrap/backfill observations are not prospective signals.",
                         "SEC duration_kind preserves quarter, annual and year-to-date differences."]}
    quality = {k: snapshot[k] for k in ("schema", "generated_at_utc", "quality", "sources", "limitations")}
    for name, doc in (("snapshot.json", snapshot), ("quality.json", quality)):
        (output / name).write_text(json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    previous = json.loads(args.previous.read_text()) if args.previous and args.previous.exists() else None
    snapshot = collect(args.output, datetime.now(timezone.utc), previous=previous, extra_sources=True)
    print(json.dumps({"quality": snapshot["quality"], "sources": [{k: s.get(k) for k in
          ("source_id", "status", "latest_period", "error_type")} for s in snapshot["sources"]]}))
    return 1 if snapshot["quality"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
