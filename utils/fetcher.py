from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from typing import Optional



BASE_URL = "https://webbsite.0xmd.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

KNOWN_ISSUE_ID_BY_STOCK = {
    "03321": "27882",
    "06080": "25298",
    "01417": "25486",
    "01953": "29176",
    "01682": "6191",
    "00524": "1061",
}


@dataclass
class FetchResult:
    name: str
    url: str
    final_url: str = ""
    status: Optional[int] = None
    fetched_time: str = ""
    html: str = ""
    raw_text: str = ""
    tables: list[pd.DataFrame] = field(default_factory=list)
    method: str = ""
    ok: bool = False
    error_type: str = ""
    error_message: str = ""
    fallback_method_used: str = ""

    def to_log(self) -> dict:
        return {
            "section": self.name,
            "url": self.url,
            "final_url": self.final_url,
            "status_code": self.status,
            "fetched_time": self.fetched_time,
            "fetch_method": self.method,
            "fallback_method_used": self.fallback_method_used,
            "ok": self.ok,
            "tables_found": len(self.tables),
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass
class IssueLookup:
    stock_code: str
    issue_id: str = ""
    method: str = ""
    status: str = ""
    message: str = ""
    result: Optional[FetchResult] = None


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def orgdata_url(stock_code: str) -> str:
    return f"{BASE_URL}/dbpub/orgdata.asp?code={clean_stock_code(stock_code)}&Submit=current"


def issue_urls(issue_id: str) -> dict[str, str]:
    return {
        "Holdings": f"{BASE_URL}/ccass/choldings.asp?i={issue_id}",
        "Changes": f"{BASE_URL}/ccass/chldchg.asp?i={issue_id}",
        "Big Changes": f"{BASE_URL}/ccass/bigchangesissue.asp?i={issue_id}",
        "Concentration": f"{BASE_URL}/ccass/cconchist.asp?i={issue_id}",
    }


def clean_stock_code(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits.zfill(5) if digits else ""


def looks_like_issue_id(value: str) -> bool:
    text = (value or "").strip()
    return bool(re.fullmatch(r"\d{4,8}", text)) and not text.startswith("0")


def extract_tables_from_html(html: str) -> list[pd.DataFrame]:
    import pandas as pd

    if not html:
        return []
    tables = pd.read_html(StringIO(html), flavor="lxml")
    cleaned = []
    for table in tables:
        table = table.copy()
        if isinstance(table.columns, pd.MultiIndex):
            table.columns = [
                " ".join(str(part).strip() for part in col if str(part).strip() and not str(part).startswith("Unnamed"))
                for col in table.columns
            ]
        else:
            table.columns = [str(col).strip() for col in table.columns]
        table = table.dropna(how="all")
        table = table.loc[:, ~pd.Index(table.columns).astype(str).str.fullmatch(r"Unnamed:.*", na=False)]
        cleaned.append(table)
    return cleaned


def html_to_text(html: str, limit: int = 8000) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "lxml")
    text = soup.get_text("\n", strip=True)
    return text[:limit]


def fetch_with_requests(name: str, url: str, timeout: int) -> FetchResult:
    import requests

    result = FetchResult(name=name, url=url, fetched_time=now_iso(), method="requests")
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        )
        result.status = response.status_code
        result.final_url = response.url
        response.raise_for_status()
        if response.apparent_encoding:
            response.encoding = response.apparent_encoding
        result.html = response.text
        result.raw_text = html_to_text(response.text)
        result.tables = extract_tables_from_html(response.text)
        if not result.tables:
            raise ValueError("no table found")
        result.ok = True
    except Exception as exc:
        result.error_type = type(exc).__name__
        result.error_message = str(exc)
        result.ok = False
    return result


def fetch_with_playwright(name: str, url: str, timeout: int, headless: bool) -> FetchResult:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    result = FetchResult(name=name, url=url, fetched_time=now_iso(), method="playwright")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1440, "height": 1000},
                locale="en-US",
            )
            page = context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            page.wait_for_timeout(750)
            result.final_url = page.url
            result.status = response.status if response else None
            result.html = page.content()
            result.raw_text = html_to_text(result.html)
            result.tables = extract_tables_from_html(result.html)
            browser.close()
            if not result.tables:
                raise ValueError("no table found")
            result.ok = True
    except (PlaywrightTimeoutError, PlaywrightError, Exception) as exc:
        result.error_type = type(exc).__name__
        result.error_message = str(exc)
        result.ok = False
    return result


def fetch_page(name: str, url: str, timeout: int = 60, headless: bool = True) -> FetchResult:
    first = fetch_with_requests(name, url, timeout=timeout)
    if first.ok:
        return first

    fallback_reasons = ("403", "timeout", "no table", "dns", "connection", "name resolution")
    error_text = f"{first.status} {first.error_message}".lower()
    should_try_browser = any(reason in error_text for reason in fallback_reasons) or not first.tables
    if not should_try_browser:
        return first

    second = fetch_with_playwright(name, url, timeout=timeout, headless=headless)
    second.fallback_method_used = "requests -> playwright"
    if second.ok:
        return second
    second.error_type = second.error_type or first.error_type
    second.error_message = second.error_message or first.error_message
    return second


def extract_issue_id_from_html(html: str) -> tuple[str, str]:
    from bs4 import BeautifulSoup

    if not html:
        return "", ""

    patterns = [
        r"(?:choldings|chldchg|bigchangesissue|cconchist)\.asp\?[^\"'>]*[?&]i=(\d+)",
        r"(?:totalreturn|dealings|trades|price|changes)[^\"'>]*[?&]i=(\d+)",
        r"[?&]i=(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.I)
        if match:
            method = "extracted from orgdata" if "ccass" in match.group(0).lower() else "extracted from URL"
            return match.group(1), method

    soup = BeautifulSoup(html, "lxml")
    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = link.get_text(" ", strip=True).lower()
        if not any(token in f"{href.lower()} {text}" for token in ("ccass", "total return", "dealings", "securities")):
            continue
        match = re.search(r"[?&]i=(\d+)", href)
        if match:
            return match.group(1), "extracted from URL"
    return "", ""


def resolve_issue_id_from_stock(stock_code: str, timeout: int = 60, headless: bool = True) -> IssueLookup:
    code = clean_stock_code(stock_code)
    result = fetch_page("Company / orgdata", orgdata_url(code), timeout=timeout, headless=headless)
    issue_id, method = extract_issue_id_from_html(result.html)
    if issue_id:
        return IssueLookup(stock_code=code, issue_id=issue_id, method=method, status="success", result=result)

    if code in KNOWN_ISSUE_ID_BY_STOCK:
        return IssueLookup(
            stock_code=code,
            issue_id=KNOWN_ISSUE_ID_BY_STOCK[code],
            method="known mapping fallback",
            status="success",
            message="Issue ID was not found in orgdata links; known mapping fallback was used.",
            result=result,
        )

    return IssueLookup(
        stock_code=code,
        method="",
        status="failed",
        message="Cannot automatically determine Webb-site issue ID. Please enter the Webb-site Issue ID manually.",
        result=result,
    )


def resolve_issue_id(value: str, input_type: str = "Stock Code", timeout: int = 60, headless: bool = True) -> IssueLookup:
    if input_type == "Webb-site Issue ID" or looks_like_issue_id(value):
        issue_id = (value or "").strip()
        return IssueLookup(
            stock_code="",
            issue_id=issue_id,
            method="manually entered",
            status="success",
            message="Issue ID was manually entered.",
        )
    return resolve_issue_id_from_stock(value, timeout=timeout, headless=headless)


def fetch_all(issue_id: str, stock_code: str = "", timeout: int = 60, headless: bool = True, delay_seconds: Optional[float] = None) -> dict[str, FetchResult]:
    delay = float(os.getenv("FETCH_DELAY_SECONDS", delay_seconds if delay_seconds is not None else 0.5))
    results: dict[str, FetchResult] = {}
    if stock_code:
        results["Company / orgdata"] = fetch_page("Company / orgdata", orgdata_url(stock_code), timeout=timeout, headless=headless)
        time.sleep(max(delay, 0))
    for name, url in issue_urls(issue_id).items():
        results[name] = fetch_page(name, url, timeout=timeout, headless=headless)
        time.sleep(max(delay, 0))
    return results
