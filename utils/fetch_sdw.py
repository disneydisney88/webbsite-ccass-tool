from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .fetcher import USER_AGENT, clean_stock_code, now_iso
from .parse_sdw import SDWParseError, SDWSnapshot, parse_sdw_snapshot


SDW_URL = "https://www3.hkexnews.hk/sdw/search/searchsdw.aspx"


class SDWFetchError(RuntimeError):
    pass


@dataclass
class SDWFetchResult:
    ok: bool
    url: str = SDW_URL
    final_url: str = ""
    status_code: int | None = None
    fetched_at: str = ""
    html: str = ""
    snapshot: SDWSnapshot | None = None
    error_type: str = ""
    error_message: str = ""


def normalize_sdw_date(value: str | None) -> str:
    if value:
        parsed = datetime.strptime(value.replace("-", "/"), "%Y/%m/%d")
        return parsed.strftime("%Y/%m/%d")
    return ""


def _initial_payload(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    payload: dict[str, str] = {}
    for input_tag in soup.find_all("input"):
        name = input_tag.get("name")
        if not name:
            continue
        payload[name] = input_tag.get("value", "")
    return payload


def _default_query_date(payload: dict[str, str]) -> str:
    value = payload.get("txtShareholdingDate", "")
    if not value:
        raise SDWFetchError("SOURCE_CHANGED: SDW form did not expose txtShareholdingDate")
    return value


def fetch_sdw_snapshot(stock_code: str, query_date: str | None = None, timeout: int = 30) -> SDWFetchResult:
    code = clean_stock_code(stock_code)
    result = SDWFetchResult(ok=False, fetched_at=now_iso())
    if not code:
        result.error_type = "INVALID_INPUT"
        result.error_message = "Stock code is required for HKEX SDW mode."
        return result

    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": SDW_URL,
    }
    session = requests.Session()
    try:
        first = session.get(SDW_URL, headers=headers, timeout=timeout)
        result.status_code = first.status_code
        result.final_url = first.url
        if first.status_code >= 400:
            raise SDWFetchError(f"SDW GET returned HTTP {first.status_code}")

        payload = _initial_payload(first.text)
        sdw_date = normalize_sdw_date(query_date) if query_date else _default_query_date(payload)
        payload.update(
            {
                "__EVENTTARGET": "btnSearch",
                "__EVENTARGUMENT": "",
                "txtShareholdingDate": sdw_date,
                "txtStockCode": code,
                "txtStockName": "",
                "txtParticipantID": "",
                "txtParticipantName": "",
                "txtSelPartID": "",
            }
        )
        time.sleep(2.0)
        response = session.post(SDW_URL, data=payload, headers=headers, timeout=timeout)
        result.status_code = response.status_code
        result.final_url = response.url
        result.html = response.text
        if response.status_code >= 400:
            raise SDWFetchError(f"SDW POST returned HTTP {response.status_code}")

        result.snapshot = parse_sdw_snapshot(response.text, code=code, query_date=sdw_date)
        result.ok = True
    except (requests.RequestException, SDWFetchError, SDWParseError, ValueError) as exc:
        result.ok = False
        result.error_type = type(exc).__name__
        result.error_message = str(exc)
    return result
