from __future__ import annotations

import hashlib
import os
import re
import time
import json
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Optional

# 0xmd is temporarily blocked by Cloudflare. Keep it configurable, but use the
# compatible mirror until it becomes available again.
DEFAULT_BASE_URL = "https://webb-database.com"
BASE_URL = DEFAULT_BASE_URL
_PLAYWRIGHT_INSTALL_LOCK = threading.Lock()
_PLAYWRIGHT_INSTALL_ATTEMPTED = False
_PLAYWRIGHT_INSTALL_READY = False
_PLAYWRIGHT_INSTALL_ERROR = ""
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
DETERMINISTIC_FAILURE_TYPES = {"SOURCE_CHALLENGE", "MIRROR_BLOCKED"}
DETERMINISTIC_FAILURE_STATUS_CODES = {402, 403}
WEBB_DATABASE_CHALLENGE_COOKIE_NAME = "ayuus"
WEBB_DATABASE_CHALLENGE_COOKIE_MAGIC = "94run15wglA7NegzhIu4D"
WEBB_DATABASE_CHALLENGE_COOKIE_TTL_SECONDS = 600


def chromium_unavailable_error(exc: BaseException | str) -> bool:
    """Return whether a Playwright launch failed because Chromium cannot run."""
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "error while loading shared libraries",
            "libglib-2.0.so.0",
            "exitcode=127",
            "exit code 127",
            "executable doesn't exist",
            "browser executable",
            "playwright chromium is unavailable",
            "automatic installation failed",
        )
    )


def ensure_playwright_chromium() -> tuple[bool, str]:
    """Install Chromium once, only after a mirror page needs browser rendering."""
    global _PLAYWRIGHT_INSTALL_ATTEMPTED, _PLAYWRIGHT_INSTALL_READY, _PLAYWRIGHT_INSTALL_ERROR

    with _PLAYWRIGHT_INSTALL_LOCK:
        if _PLAYWRIGHT_INSTALL_READY:
            return True, ""
        if _PLAYWRIGHT_INSTALL_ATTEMPTED:
            return False, _PLAYWRIGHT_INSTALL_ERROR or "Chromium installation was already attempted."

        _PLAYWRIGHT_INSTALL_ATTEMPTED = True
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except Exception as exc:
            _PLAYWRIGHT_INSTALL_ERROR = f"Could not install Chromium: {exc}"
            return False, _PLAYWRIGHT_INSTALL_ERROR

        if completed.returncode == 0:
            _PLAYWRIGHT_INSTALL_READY = True
            return True, ""

        detail = (completed.stderr or completed.stdout or "unknown installer error").strip().replace("\n", " ")
        _PLAYWRIGHT_INSTALL_ERROR = f"Could not install Chromium (exit {completed.returncode}): {detail[-500:]}"
        return False, _PLAYWRIGHT_INSTALL_ERROR

KNOWN_ISSUE_ID_BY_STOCK = {
    "03321": "27882",
    "06080": "25298",
    "01417": "25486",
    "01953": "29176",
    "01682": "6191",
    "00524": "1061",
    "01592": "26603",
    "06162": "27470",
    "01912": "28222",
}


@dataclass
class FetchResult:
    name: str
    url: str
    final_url: str = ""
    status: Optional[int] = None
    status_reason: str = ""
    attempts: int = 0
    fetched_time: str = ""
    html: str = ""
    raw_text: str = ""
    response_snippet: str = ""
    tables: list[pd.DataFrame] = field(default_factory=list)
    method: str = ""
    ok: bool = False
    error_type: str = ""
    error_message: str = ""
    fallback_method_used: str = ""
    attempted_sources: list[dict[str, Any]] = field(default_factory=list)
    skipped: bool = False

    def to_log(self) -> dict:
        return {
            "section": self.name,
            "url": self.url,
            "final_url": self.final_url,
            "status_code": self.status,
            "status_reason": self.status_reason,
            "attempts": self.attempts,
            "fetched_time": self.fetched_time,
            "fetch_method": self.method,
            "fallback_method_used": self.fallback_method_used,
            "ok": self.ok,
            "tables_found": len(self.tables),
            "error_type": self.error_type,
            "error_message": self.error_message,
            "response_snippet": self.response_snippet,
            "attempted_sources": getattr(self, "attempted_sources", []),
            "skipped": getattr(self, "skipped", False),
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


def mirror_base_url() -> str:
    """Configured Webb-site-compatible mirror base URL.

    Keep the original 0xmd mirror as the default. A replacement mirror can be
    tested by setting CCASS_MIRROR_BASE_URL before app startup, or by adding
    CCASS_MIRROR_BASE_URL / mirror_base_url to ccass_source_config.json.
    """
    value = os.getenv("CCASS_MIRROR_BASE_URL", "").strip()
    if not value:
        config_path = Path(os.getenv("CCASS_SOURCE_CONFIG", "ccass_source_config.json"))
        try:
            data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
            value = str(data.get("CCASS_MIRROR_BASE_URL") or data.get("mirror_base_url") or "").strip()
        except (OSError, json.JSONDecodeError):
            value = ""
    value = value or BASE_URL
    return value.rstrip("/")


def orgdata_url(stock_code: str) -> str:
    return f"{mirror_base_url()}/dbpub/orgdata.asp?code={clean_stock_code(stock_code)}&Submit=current"


def issue_urls(issue_id: str) -> dict[str, str]:
    base = mirror_base_url()
    return {
        "Holdings": f"{base}/ccass/choldings.asp?i={issue_id}",
        "Changes": f"{base}/ccass/chldchg.asp?i={issue_id}",
        "Big Changes": f"{base}/ccass/bigchangesissue.asp?i={issue_id}",
        "Concentration": f"{base}/ccass/cconchist.asp?i={issue_id}",
        "Price History": f"{base}/dbpub/hpu.asp?i={issue_id}",
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


def body_head(text: str, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def _write_debug_artifact(
    name: str,
    url: str,
    source: str,
    html: str = "",
    status: Optional[int] = None,
    headers=None,
    stock_code: str = "",
    final_url: str = "",
) -> None:
    """Persist one non-git-tracked fetch diagnostic for Holdings/Changes only."""
    if os.getenv("CCASS_DEBUG_DUMP", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    if name not in {"Holdings", "Changes"}:
        return
    code = clean_stock_code(stock_code) or "unknown"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    folder = Path("debug") / code
    try:
        folder.mkdir(parents=True, exist_ok=True)
        html_path = folder / f"{name}_{source}_{timestamp}.html"
        meta_path = folder / f"{name}_{source}_{timestamp}.meta.json"
        html_path.write_text(html or "", encoding="utf-8", errors="replace")
        header_map = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
        tables = []
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html or "", "lxml")
            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                cols = max((len(row.find_all(["th", "td"])) for row in rows), default=0)
                tables.append({"rows": len(rows), "cols": cols})
        except Exception:
            tables = []
        meta = {
            "name": name,
            "source": source,
            "url": url,
            "final_url": final_url or url,
            "http_status": status,
            "response_length": len(html or ""),
            "content_type": header_map.get("content-type", ""),
            "has_set_cookie": "set-cookie" in header_map,
            "response_head": (html or "")[:500],
            "tables_found": len(tables),
            "tables": tables,
            "html_file": str(html_path).replace("\\", "/"),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        # Diagnostics must never change the fetch result on a read-only host.
        return


def webb_database_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def is_webb_database_url(url: str) -> bool:
    host = webb_database_host(url)
    return host == "webb-database.com" or host.endswith(".webb-database.com")


def webb_database_cookie_value(user_agent: str, now_ms: Optional[int] = None) -> str:
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    day_bucket = str(int(current_ms / 1000 / 86400))
    inner = hashlib.md5((day_bucket + user_agent + WEBB_DATABASE_CHALLENGE_COOKIE_MAGIC).encode("utf-8")).hexdigest()
    return hashlib.md5((day_bucket + WEBB_DATABASE_CHALLENGE_COOKIE_MAGIC + inner).encode("utf-8")).hexdigest()


def looks_like_js_cookie_challenge(html: str) -> bool:
    text = (html or "").lower()
    return bool(
        ("setcookie()" in text and "location.reload" in text)
        or ("document.cookie" in text and "location.reload" in text)
        or ("please enable cookies" in text)
        or ("js required" in text)
    )


def upstream_failure_message(result: FetchResult) -> str:
    parts = []
    if result.status is not None:
        status_text = f"HTTP {result.status}"
        if result.status_reason:
            status_text += f" {result.status_reason}"
        parts.append(status_text)
    if result.final_url and result.final_url != result.url:
        parts.append(f"final_url={result.final_url}")
    else:
        parts.append(f"url={result.url}")
    if result.response_snippet:
        parts.append(f"body_head={result.response_snippet}")
    return "; ".join(parts)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def fetch_with_requests(name: str, url: str, timeout: int, debug_stock: str = "") -> FetchResult:
    import requests

    attempts = max(1, _int_env("FETCH_RETRY_ATTEMPTS", 3))
    backoff = max(0.0, _float_env("FETCH_RETRY_BACKOFF_SECONDS", 0.75))
    result = FetchResult(name=name, url=url, fetched_time=now_iso(), method="requests")
    last_error_type = ""
    last_error_message = ""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})

    def populate_from_response(response) -> None:
        result.status = response.status_code
        result.status_reason = response.reason or ""
        result.final_url = response.url
        if response.apparent_encoding:
            response.encoding = response.apparent_encoding
        result.html = response.text
        result.raw_text = html_to_text(response.text)
        result.response_snippet = body_head(response.text)
        _write_debug_artifact(name, url, "requests", response.text, response.status_code, getattr(response, "headers", {}), debug_stock, result.final_url)

    for attempt in range(1, attempts + 1):
        result.attempts = attempt
        result.method = "requests" if attempt == 1 else f"requests retry {attempt}/{attempts}"
        try:
            response = session.get(url, timeout=timeout)
            populate_from_response(response)
            challenge_text = f"{response.status_code} {response.text[:1000]}".lower()
            if response.status_code == 403 or "cloudflare" in challenge_text or "turnstile" in challenge_text or "cf-chl" in challenge_text:
                result.error_type = "MIRROR_BLOCKED"
                result.error_message = f"Webb-site mirror returned 403 or human verification challenge. {upstream_failure_message(result)}"
                result.ok = False
                return result
            if response.status_code >= 400:
                result.error_type = "HTTPError"
                result.error_message = upstream_failure_message(result)
                result.ok = False
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                    time.sleep(backoff * (2 ** (attempt - 1)))
                    continue
                return result
            if looks_like_js_cookie_challenge(response.text):
                if is_webb_database_url(url):
                    cookie_value = webb_database_cookie_value(USER_AGENT)
                    host = webb_database_host(url)
                    session.cookies.set(WEBB_DATABASE_CHALLENGE_COOKIE_NAME, cookie_value, domain=host, path="/")
                    if host.endswith(".webb-database.com") and host != "webb-database.com":
                        session.cookies.set(
                            WEBB_DATABASE_CHALLENGE_COOKIE_NAME,
                            cookie_value,
                            domain=".webb-database.com",
                            path="/",
                        )
                    solved = session.get(url, timeout=timeout)
                    populate_from_response(solved)
                    if not looks_like_js_cookie_challenge(solved.text):
                        response = solved
                        result.fallback_method_used = "webb-database challenge cookie"
                    else:
                        result.error_type = "JS_CHALLENGE"
                        result.error_message = (
                            "Upstream returned a JavaScript cookie/reload challenge even after challenge cookie retry. "
                            f"{upstream_failure_message(result)}"
                        )
                        result.ok = False
                        return result
                else:
                    result.error_type = "JS_CHALLENGE"
                    result.error_message = f"Upstream returned a JavaScript cookie/reload challenge instead of data. {upstream_failure_message(result)}"
                    result.ok = False
                    return result
            result.tables = extract_tables_from_html(response.text)
            if not result.tables:
                result.error_type = "ValueError"
                result.error_message = f"no table found. {upstream_failure_message(result)}"
                result.ok = False
                return result
            result.ok = True
            return result
        except requests.RequestException as exc:
            response = getattr(exc, "response", None)
            if response is not None:
                result.status = response.status_code
                result.status_reason = response.reason or ""
                result.final_url = response.url
                result.response_snippet = body_head(getattr(response, "text", ""))
            last_error_type = type(exc).__name__
            last_error_message = str(exc)
            if attempt < attempts:
                time.sleep(backoff * (2 ** (attempt - 1)))
                continue
        except Exception as exc:
            last_error_type = type(exc).__name__
            last_error_message = str(exc)
            break
    result.error_type = last_error_type or result.error_type or "FetchError"
    result.error_message = last_error_message or result.error_message or upstream_failure_message(result)
    _write_debug_artifact(name, url, "requests", result.html, result.status, {}, debug_stock)
    result.ok = False
    return result


def fetch_with_playwright(name: str, url: str, timeout: int, headless: bool, debug_stock: str = "") -> FetchResult:
    result = FetchResult(name=name, url=url, fetched_time=now_iso(), method="playwright")
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=headless)
            except Exception as launch_exc:
                message = str(launch_exc).lower()
                missing_executable = "executable doesn't exist" in message or "browser executable" in message
                if missing_executable:
                    installed, install_error = ensure_playwright_chromium()
                    if not installed:
                        raise RuntimeError(
                            "Playwright Chromium is unavailable and automatic installation failed. "
                            f"{install_error}"
                        ) from launch_exc
                    browser = p.chromium.launch(headless=headless)
                    result.fallback_method_used = "playwright installed Chromium on demand"
                else:
                    missing_headless_shell = "chromium_headless_shell" in message or "chrome-headless-shell" in message
                    if not headless or not missing_headless_shell:
                        raise
                    browser = p.chromium.launch(headless=False)
                    result.fallback_method_used = "playwright headless -> headed"
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
            _write_debug_artifact(name, url, "playwright", result.html, result.status, getattr(response, "headers", {}) if response else {}, debug_stock, result.final_url)
            browser.close()
            if not result.tables:
                raise ValueError("no table found")
            result.ok = True
    except Exception as exc:
        if type(exc).__name__ == "ImportError" or chromium_unavailable_error(exc):
            result.error_type = "CHROMIUM_UNAVAILABLE"
        else:
            result.error_type = type(exc).__name__
        if type(exc).__name__ == "ImportError":
            result.error_message = "Playwright is not installed; mirror browser fallback is unavailable."
        else:
            result.error_message = str(exc)
        result.ok = False
    if not result.html:
        _write_debug_artifact(name, url, "playwright", "", result.status, {}, debug_stock)
    return result


def fetch_page(name: str, url: str, timeout: int = 60, headless: bool = True, debug_stock: str = "") -> FetchResult:
    first = fetch_with_requests(name, url, timeout=timeout, debug_stock=debug_stock)
    if first.ok:
        return first
    if first.error_type in DETERMINISTIC_FAILURE_TYPES or first.status in DETERMINISTIC_FAILURE_STATUS_CODES:
        return first

    fallback_reasons = ("403", "timeout", "no table", "js_challenge", "dns", "connection", "name resolution")
    error_text = f"{first.status} {first.error_message}".lower()
    should_try_browser = any(reason in error_text for reason in fallback_reasons) or not first.tables
    if not should_try_browser:
        return first

    second = fetch_with_playwright(name, url, timeout=timeout, headless=headless, debug_stock=debug_stock)
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
    # The orgdata fetch is the gateway to everything else, so give it a couple
    # of plain-HTTP retries with backoff before giving up - the source
    # intermittently rate-limits bursts (e.g. right after a rainbow build).
    result = fetch_page("Company / orgdata", orgdata_url(code), timeout=timeout, headless=headless)
    issue_id, method = extract_issue_id_from_html(result.html)
    for delay in (1.5, 3.0):
        if issue_id:
            break
        time.sleep(delay)
        retry = fetch_with_requests("Company / orgdata", orgdata_url(code), timeout=timeout)
        if retry.html:
            result = retry
            issue_id, method = extract_issue_id_from_html(retry.html)
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
        results["Company / orgdata"] = fetch_page("Company / orgdata", orgdata_url(stock_code), timeout=timeout, headless=headless, debug_stock=stock_code)
        time.sleep(max(delay, 0))
    for name, url in issue_urls(issue_id).items():
        results[name] = fetch_page(name, url, timeout=timeout, headless=headless, debug_stock=stock_code)
        time.sleep(max(delay, 0))
    return results
