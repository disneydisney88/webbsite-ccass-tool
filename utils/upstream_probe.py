from __future__ import annotations

import re
import time
from typing import Any

import requests

from .f10_equity import f10_equity_url
from .fetcher import USER_AGENT, body_head, issue_urls, mirror_base_url
from .hkexnews import BASE_URL as HKEXNEWS_BASE_URL


HKEX_SDW_URL = "https://www3.hkexnews.hk/sdw/search/searchsdw.aspx"


HEADER_ALLOWLIST = {
    "cache-control",
    "content-length",
    "content-type",
    "date",
    "location",
    "server",
    "set-cookie",
    "strict-transport-security",
    "x-powered-by",
}


def compact_headers(headers: requests.structures.CaseInsensitiveDict[str]) -> dict[str, str]:
    compact: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in HEADER_ALLOWLIST:
            compact[key] = re.sub(r"\s+", " ", str(value))[:300]
    return compact


def probe_url(source: str, url: str, timeout: int = 8) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {
        "source": source,
        "ok": False,
        "url": url,
        "final_url": "",
        "status_code": None,
        "reason": "",
        "elapsed_ms": 0,
        "headers": {},
        "body_head": "",
        "error_type": "",
        "error_message": "",
    }
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        )
        result.update(
            {
                "ok": response.status_code < 400,
                "final_url": response.url,
                "status_code": response.status_code,
                "reason": response.reason or "",
                "headers": compact_headers(response.headers),
                "body_head": body_head(response.text),
            }
        )
        body = str(result["body_head"]).lower()
        if response.status_code < 400 and ("setcookie()" in body or "location.reload" in body):
            result["ok"] = False
            result["error_type"] = "SOURCE_CHALLENGE"
            result["error_message"] = "Upstream returned a JavaScript cookie/reload challenge instead of data."
        elif response.status_code >= 400:
            result["error_type"] = "HTTPError"
            result["error_message"] = f"HTTP {response.status_code} {response.reason}"
    except requests.RequestException as exc:
        response = getattr(exc, "response", None)
        if response is not None:
            result.update(
                {
                    "final_url": response.url,
                    "status_code": response.status_code,
                    "reason": response.reason or "",
                    "headers": compact_headers(response.headers),
                    "body_head": body_head(getattr(response, "text", "")),
                }
            )
        result["error_type"] = type(exc).__name__
        result["error_message"] = str(exc)
    finally:
        result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return result


def probe_upstreams(stock_code: str = "01592", issue_id: str = "26603", timeout: int = 8) -> dict[str, Any]:
    return {
        "mirror_base_url": mirror_base_url(),
        "probes": [
            probe_url("webbsite_holdings", issue_urls(issue_id)["Holdings"], timeout=timeout),
            probe_url("hkex_sdw", HKEX_SDW_URL, timeout=timeout),
            probe_url("hkexnews_active_stock", f"{HKEXNEWS_BASE_URL}/ncms/script/eds/activestock_sehk_c.json", timeout=timeout),
            probe_url("f10_equity", f10_equity_url(stock_code), timeout=timeout),
        ],
    }
