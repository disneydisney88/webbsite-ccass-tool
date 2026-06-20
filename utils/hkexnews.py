from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd
import requests


BASE_URL = "https://www1.hkexnews.hk"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class HKEXAnnouncementResult:
    stock_code: str
    stock_name: str = ""
    hkex_stock_id: str = ""
    period_years: int = 1
    from_date: str = ""
    to_date: str = ""
    url: str = ""
    ok: bool = False
    error: str = ""
    total_count: int = 0
    table: pd.DataFrame | None = None


def clean_html_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_stock_code(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits.zfill(5) if digits else ""


def hkex_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def display_date(value: str) -> str:
    try:
        return pd.to_datetime(value, format="%Y%m%d").strftime("%Y-%m-%d")
    except Exception:
        return value


def active_stock_map(timeout: int = 30) -> list[dict[str, Any]]:
    response = requests.get(
        f"{BASE_URL}/ncms/script/eds/activestock_sehk_c.json",
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"},
    )
    response.raise_for_status()
    return response.json()


def resolve_hkex_stock(stock_code: str, timeout: int = 30) -> tuple[str, str]:
    code = clean_stock_code(stock_code)
    for item in active_stock_map(timeout=timeout):
        if clean_stock_code(str(item.get("c", ""))) == code:
            return str(item.get("i", "")), str(item.get("n", ""))
    return "", ""


def parse_result_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_result = payload.get("result")
    if not raw_result or raw_result == "null":
        return []
    if isinstance(raw_result, list):
        return raw_result
    return json.loads(raw_result)


def normalize_announcements(rows: list[dict[str, Any]]) -> pd.DataFrame:
    normalized = []
    for row in rows:
        file_link = str(row.get("FILE_LINK", "") or "")
        normalized.append(
            {
                "Publish time": row.get("DATE_TIME", ""),
                "Stock code": row.get("STOCK_CODE", ""),
                "Stock name": row.get("STOCK_NAME", ""),
                "Category": clean_html_text(row.get("LONG_TEXT") or row.get("SHORT_TEXT")),
                "Title": clean_html_text(row.get("TITLE")),
                "File info": row.get("FILE_INFO", ""),
                "File type": row.get("FILE_TYPE", ""),
                "URL": f"{BASE_URL}{file_link}" if file_link.startswith("/") else file_link,
                "News ID": row.get("NEWS_ID", ""),
            }
        )
    return pd.DataFrame(normalized)


def fetch_announcements(stock_code: str, period_years: int = 1, timeout: int = 30, row_range: int = 100) -> HKEXAnnouncementResult:
    code = clean_stock_code(stock_code)
    today = date.today()
    from_day = today - timedelta(days=365 * max(int(period_years), 1))
    result = HKEXAnnouncementResult(
        stock_code=code,
        period_years=period_years,
        from_date=display_date(hkex_date(from_day)),
        to_date=display_date(hkex_date(today)),
    )
    try:
        stock_id, stock_name = resolve_hkex_stock(code, timeout=timeout)
        result.hkex_stock_id = stock_id
        result.stock_name = stock_name
        if not stock_id:
            result.error = f"HKEX stockId not found for stock code {code}."
            result.table = pd.DataFrame()
            return result

        params = {
            "sortDir": "0",
            "sortByOptions": "DateTime",
            "category": "0",
            "market": "SEHK",
            "stockId": stock_id,
            "documentType": "-1",
            "fromDate": hkex_date(from_day),
            "toDate": hkex_date(today),
            "title": "",
            "searchType": "0",
            "t1code": "-2",
            "t2Gcode": "-2",
            "t2code": "-2",
            "rowRange": str(row_range),
            "lang": "zh",
        }
        response = requests.get(
            f"{BASE_URL}/search/titleSearchServlet.do",
            params=params,
            timeout=timeout,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
                "Referer": f"{BASE_URL}/search/titlesearch.xhtml?lang=zh",
            },
        )
        result.url = response.url
        response.raise_for_status()
        payload = response.json()
        rows = parse_result_payload(payload)
        result.table = normalize_announcements(rows)
        result.total_count = int(rows[0].get("TOTAL_COUNT", len(rows))) if rows else 0
        result.ok = True
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        result.table = pd.DataFrame()
    return result
