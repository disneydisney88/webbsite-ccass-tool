from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .fetch_sdw import SDW_URL, fetch_sdw_snapshot
from .fetch_yahoo import fetch_yahoo_price_history
from .fetcher import (
    FetchResult,
    IssueLookup,
    KNOWN_ISSUE_ID_BY_STOCK,
    clean_stock_code,
    fetch_all,
    fetch_with_playwright,
    fetch_with_requests,
    issue_urls,
    mirror_base_url,
    orgdata_url,
    resolve_issue_id_from_stock,
)
from .snapshot_db import DB_PATH, build_results_from_db, db_restore_status, history_depth_days, load_price_history, snapshot_exists, stock_fetched_today, upsert_price_history, upsert_snapshot


CONFIG_PATH = Path(os.getenv("CCASS_SOURCE_CONFIG", "ccass_source_config.json"))
PROBE_CACHE_PATH = Path(os.getenv("CCASS_MIRROR_PROBE_CACHE", "data/mirror_probe_status.json"))
VALID_MODES = {"auto", "mirror", "sdw"}


@dataclass
class SourceBundle:
    lookup: IssueLookup
    results: dict[str, FetchResult]
    metadata: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def get_source_mode() -> str:
    env_value = os.getenv("CCASS_SOURCE_MODE", "").strip().lower()
    if env_value in VALID_MODES:
        return env_value
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            value = str(data.get("CCASS_SOURCE_MODE") or data.get("source_mode") or "").strip().lower()
            if value in VALID_MODES:
                return value
        except (OSError, json.JSONDecodeError):
            pass
    return "auto"


def _is_cloudflare_challenge(result: FetchResult) -> bool:
    text = f"{result.status} {result.error_type} {result.error_message} {result.raw_text[:500]} {result.html[:500]}".lower()
    return result.status == 403 or "cloudflare" in text or "turnstile" in text or "cf-chl" in text


def _probe_cache_today() -> dict[str, object] | None:
    if not PROBE_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(PROBE_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if data.get("date") == date.today().isoformat() and data.get("mirror_base_url") == mirror_base_url() else None


def _write_probe_cache(status: str) -> None:
    PROBE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROBE_CACHE_PATH.write_text(
        json.dumps({"date": date.today().isoformat(), "status": status, "mirror_base_url": mirror_base_url()}, indent=2),
        encoding="utf-8",
    )


def mirror_probe(stock_code: str, issue_id: str, timeout: int, headless: bool = True) -> str:
    cached = _probe_cache_today()
    if cached:
        return str(cached.get("status") or "unknown")
    url = issue_urls(issue_id).get("Holdings") if issue_id else orgdata_url(stock_code)
    result = fetch_with_requests("Mirror probe", url, timeout=max(3, min(timeout, 8)))
    if _is_cloudflare_challenge(result):
        status = "blocked_by_cloudflare"
    elif result.ok:
        status = "ok"
    elif result.status == 200:
        browser_result = fetch_with_playwright("Mirror probe", url, timeout=max(10, min(timeout, 45)), headless=headless)
        status = "ok" if browser_result.ok else "failed"
    else:
        status = "failed"
    _write_probe_cache(status)
    return status


def issue_id_for_stock(code: str) -> str:
    return KNOWN_ISSUE_ID_BY_STOCK.get(clean_stock_code(code), "")


def stock_code_for_issue_id(issue_id: str) -> str:
    reverse = {issue: code for code, issue in KNOWN_ISSUE_ID_BY_STOCK.items()}
    return reverse.get(str(issue_id), "")


def fetch_sdw_bundle(stock_code: str, issue_id: str = "", timeout: int = 30, mirror_status: str = "") -> SourceBundle:
    code = clean_stock_code(stock_code)
    lookup = IssueLookup(
        stock_code=code,
        issue_id=issue_id or issue_id_for_stock(code),
        method="known mapping fallback" if issue_id_for_stock(code) else "sdw stock code",
        status="success" if code else "failed",
        message="HKEX SDW source selected; Webb-site mirror historical pages are unavailable in this mode.",
    )
    warnings: list[str] = []
    if not code:
        return SourceBundle(
            lookup=lookup,
            results={},
            metadata={"source": "sdw+local_db", "mirror_status": mirror_status, "mirror_base_url": mirror_base_url()},
            warnings=["Stock code is required for SDW mode."],
        )

    if not stock_fetched_today(code):
        fetch_result = fetch_sdw_snapshot(code, timeout=timeout)
        if fetch_result.ok and fetch_result.snapshot:
            if not snapshot_exists(code, fetch_result.snapshot.date):
                upsert_snapshot(fetch_result.snapshot, source="sdw")
        else:
            warnings.append(f"SDW fetch failed: {fetch_result.error_type} - {fetch_result.error_message}")

    if load_price_history(code).empty:
        price_result = fetch_yahoo_price_history(code, period_days=int(os.getenv("YAHOO_PRICE_PERIOD_DAYS", "90")))
        if price_result.ok:
            upsert_price_history(code, price_result.table, source="yahoo")
            warnings.append(price_result.warning)
        else:
            warnings.append(f"Yahoo price fetch failed: {price_result.error_type} - {price_result.error_message}")

    built = build_results_from_db(code, SDW_URL)
    warnings.extend(built.warnings or [])
    if built.stock_name:
        lookup.message = f"{lookup.message} Stock name from SDW/local DB: {built.stock_name}"
    if built.history_depth_days <= 1 and built.latest_date:
        warnings.append(f"History limited to local snapshots since {built.latest_date}; mirror historical data unavailable")
    return SourceBundle(
        lookup=lookup,
        results=built.results,
        metadata={
            "source": "sdw+local_db",
            "mirror_status": mirror_status or "not_used",
            "mirror_base_url": mirror_base_url(),
            "history_depth_days": built.history_depth_days,
            **db_restore_status(),
        },
        warnings=warnings,
    )


def fetch_mirror_bundle(stock_code: str, issue_id: str = "", timeout: int = 30, headless: bool = True, mirror_status: str = "ok") -> SourceBundle:
    code = clean_stock_code(stock_code)
    if issue_id:
        lookup = IssueLookup(stock_code=code, issue_id=issue_id, method="manually entered", status="success")
    else:
        lookup = resolve_issue_id_from_stock(code, timeout=timeout, headless=headless)
    if lookup.status != "success" or not lookup.issue_id:
        return SourceBundle(
            lookup=lookup,
            results={},
            metadata={
                "source": "mirror",
                "mirror_status": mirror_status,
                "mirror_base_url": mirror_base_url(),
                "history_depth_days": history_depth_days(code) if code else 0,
                **db_restore_status(),
            },
            warnings=[lookup.message or "Could not resolve Webb-site issue ID."],
        )
    results = fetch_all(lookup.issue_id, stock_code=code, timeout=timeout, headless=headless)
    warnings = []
    if any(_is_cloudflare_challenge(result) for result in results.values()):
        mirror_status = "blocked_by_cloudflare"
        warnings.append("MIRROR_BLOCKED: Webb-site mirror returned 403/Cloudflare challenge.")
    price_result = results.get("Price History")
    if not price_result or not price_result.ok:
        yahoo_result = fetch_yahoo_price_history(code, period_days=int(os.getenv("YAHOO_PRICE_PERIOD_DAYS", "90")))
        if yahoo_result.ok:
            upsert_price_history(code, yahoo_result.table, source="yahoo")
            built = build_results_from_db(code, "local_db://price_history")
            if "Price History" in built.results:
                results["Price History"] = built.results["Price History"]
            warnings.append(yahoo_result.warning)
        else:
            warnings.append(f"Yahoo price fetch failed: {yahoo_result.error_type} - {yahoo_result.error_message}")
    return SourceBundle(
        lookup=lookup,
        results=results,
        metadata={
            "source": "mirror",
            "mirror_status": mirror_status,
            "mirror_base_url": mirror_base_url(),
            "history_depth_days": history_depth_days(code) if code else 0,
            **db_restore_status(),
        },
        warnings=warnings,
    )


def fetch_source_bundle_for_stock(stock_code: str, timeout: int = 30, headless: bool = True) -> SourceBundle:
    code = clean_stock_code(stock_code)
    mode = get_source_mode()
    issue_id = issue_id_for_stock(code)
    if mode == "sdw":
        return fetch_sdw_bundle(code, issue_id=issue_id, timeout=timeout, mirror_status="disabled_by_config")
    if mode == "mirror":
        return fetch_mirror_bundle(code, issue_id="", timeout=timeout, headless=headless, mirror_status="forced")

    probe_status = mirror_probe(code, issue_id, timeout=timeout, headless=headless)
    if probe_status == "ok":
        return fetch_mirror_bundle(code, issue_id="", timeout=timeout, headless=headless, mirror_status="ok")
    if probe_status == "blocked_by_cloudflare":
        bundle = fetch_sdw_bundle(code, issue_id=issue_id, timeout=timeout, mirror_status="blocked_by_cloudflare")
        bundle.warnings.append("MIRROR_BLOCKED: Webb-site mirror returned 403/Cloudflare challenge; fell back to HKEX SDW + local DB.")
        return bundle
    bundle = fetch_sdw_bundle(code, issue_id=issue_id, timeout=timeout, mirror_status=probe_status)
    bundle.warnings.append(f"Mirror probe status was {probe_status}; fell back to HKEX SDW + local DB.")
    return bundle
