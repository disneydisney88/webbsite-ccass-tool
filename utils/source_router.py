from __future__ import annotations

import json
import os
import requests
import pandas as pd
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

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
from .snapshot_db import DB_PATH, build_results_from_db, db_restore_status, history_depth_days, load_price_history, load_stock_map, stock_fetched_today, upsert_price_history, upsert_stock_map


CONFIG_PATH = Path(os.getenv("CCASS_SOURCE_CONFIG", "ccass_source_config.json"))
PROBE_CACHE_PATH = Path(os.getenv("CCASS_MIRROR_PROBE_CACHE", "data/mirror_probe_status.json"))
VALID_MODES = {"auto", "mirror", "local_db", "sdw"}
RENDER_API_DEFAULT = "https://webbsite-ccass-api.onrender.com"


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


def fetch_render_api_bundle(stock_code: str = "", issue_id: str = "", timeout: int = 60) -> SourceBundle | None:
    """Use Render's Docker/Playwright runtime when Streamlit supplies its API token."""
    token = os.getenv("CCASS_API_TOKEN", "").strip()
    api_base = os.getenv("CCASS_RENDER_API_URL", "").strip().rstrip("/")
    if not token or not api_base:
        return None
    try:
        response = requests.get(
            f"{api_base}/api/stock/full",
            params={"stock_code": clean_stock_code(stock_code), "issue_id": str(issue_id or ""), "timeout": 120},
            headers={"Authorization": f"Bearer {token}"},
            # Render Free can need about a minute to wake, then Chromium still
            # needs time for the two JS-reload CCASS pages.
            timeout=max(timeout + 90, 180),
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        detail = f"HTTP {status}" if status else type(exc).__name__
        lookup = IssueLookup(stock_code=clean_stock_code(stock_code), issue_id=str(issue_id or ""), status="failed", message=f"Render API bridge failed: {detail}")
        return SourceBundle(
            lookup=lookup,
            results={},
            metadata={"source": "render_api", "mirror_status": "render_api_unavailable", "mirror_base_url": mirror_base_url()},
            warnings=[f"Render API bridge failed: {detail}. Check Streamlit CCASS_API_TOKEN and Render deployment."],
        )
    except (TypeError, ValueError) as exc:
        lookup = IssueLookup(stock_code=clean_stock_code(stock_code), issue_id=str(issue_id or ""), status="failed", message="Render API bridge returned an invalid response.")
        return SourceBundle(
            lookup=lookup,
            results={},
            metadata={"source": "render_api", "mirror_status": "render_api_invalid_response", "mirror_base_url": mirror_base_url()},
            warnings=[f"Render API bridge returned an invalid response: {type(exc).__name__}"],
        )

    metadata = data.get("metadata") or {}
    logs = {str(row.get("section")): row for row in data.get("fetch_log") or []}
    table_keys = {"Holdings": "holdings", "Changes": "changes", "Big Changes": "bigchanges", "Concentration": "concentration", "Price History": "price_history"}
    results: dict[str, FetchResult] = {}
    for section, key in table_keys.items():
        log = logs.get(section, {})
        table = pd.DataFrame(data.get(key) or [])
        results[section] = FetchResult(
            name=section,
            url=str(log.get("url") or ""), final_url=str(log.get("final_url") or ""),
            status=log.get("status_code"), fetched_time=str(log.get("fetched_time") or ""),
            tables=[table] if not table.empty else [], method="render_api",
            ok=bool(log.get("ok", not table.empty)), error_type=str(log.get("error_type") or ""),
            error_message=str(log.get("error_message") or ""), fallback_method_used="streamlit -> render api",
        )
    company = pd.DataFrame([{"Code": metadata.get("stock_code", stock_code), "Name": metadata.get("stock_name", "")}])
    results["Company / orgdata"] = FetchResult(name="Company / orgdata", url="render_api://metadata", tables=[company], method="render_api", ok=True)
    lookup = IssueLookup(stock_code=str(metadata.get("stock_code") or stock_code), issue_id=str(metadata.get("issue_id") or ""), method=str(metadata.get("id_lookup_method") or "render api"), status=str(metadata.get("id_lookup_status") or "success"))
    return SourceBundle(lookup=lookup, results=results, metadata={**metadata, "source": "render_api"}, warnings=list(data.get("analysis_warnings") or []))


def _is_cloudflare_challenge(result: FetchResult) -> bool:
    text = f"{result.status} {result.error_type} {result.error_message} {result.raw_text[:500]} {result.html[:500]}".lower()
    return result.status == 403 or "source_challenge" in text or "cloudflare" in text or "turnstile" in text or "cf-chl" in text


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
    cleaned = clean_stock_code(code)
    cached = load_stock_map(cleaned).get("issue_id", "")
    if cached:
        return str(cached)
    return KNOWN_ISSUE_ID_BY_STOCK.get(cleaned, "")


def stock_code_for_issue_id(issue_id: str) -> str:
    reverse = {issue: code for code, issue in KNOWN_ISSUE_ID_BY_STOCK.items()}
    return reverse.get(str(issue_id), "")


def _extract_company_name(result: FetchResult | None) -> str:
    if result is None:
        return ""
    for table in result.tables or []:
        if table is None or table.empty:
            continue
        for column in ("Name", "Company", "Stock name", "Stock Name"):
            if column in table.columns:
                value = table.iloc[0].get(column)
                if value:
                    return str(value).strip()
    text = str(result.raw_text or result.html or "")
    for line in text.splitlines():
        line = line.strip()
        if line and len(line) > 4 and ("holdings limited" in line.lower() or "holdings" in line.lower() or "limited" in line.lower()):
            return line
    return ""


def resolve_issue_id(stock_code: str, timeout: int = 60, headless: bool = True, force_refresh: bool = False) -> IssueLookup:
    code = clean_stock_code(stock_code)
    cached = load_stock_map(code)
    if cached.get("issue_id") and not force_refresh:
        company_name = str(cached.get("name") or "")
        company_table = pd.DataFrame([{"Code": code, "Name": company_name}])
        return IssueLookup(
            stock_code=code,
            issue_id=str(cached.get("issue_id") or ""),
            method="cache",
            status="success",
            message="Issue ID loaded from persistent cache.",
            result=FetchResult(
                name="Company / orgdata",
                url=f"local_db://stock_map/{code}",
                tables=[company_table],
                method="cache",
                ok=True,
            ),
        )
    lookup = resolve_issue_id_from_stock(code, timeout=timeout, headless=headless)
    if lookup.status == "success" and lookup.issue_id:
        company_name = _extract_company_name(lookup.result)
        try:
            upsert_stock_map(code, lookup.issue_id, name=company_name)
        except Exception:
            pass
        lookup.message = lookup.message or "Issue ID resolved from Webb lookup."
    return lookup


def fetch_webb_price_history(
    stock_code: str,
    issue_id: str = "",
    timeout: int = 30,
    headless: bool = True,
) -> tuple[FetchResult | None, IssueLookup]:
    """Fetch price history independently from the CCASS source decision.

    The Webb-site price page can remain available even when CCASS holdings pages
    need browser rendering or the router has selected local DB for holdings.
    """
    code = clean_stock_code(stock_code)
    if issue_id:
        lookup = IssueLookup(stock_code=code, issue_id=issue_id, method="known mapping fallback", status="success")
    else:
        lookup = resolve_issue_id(code, timeout=timeout, headless=headless)
    if lookup.status != "success" or not lookup.issue_id:
        return None, lookup

    result = fetch_with_requests("Price History", issue_urls(lookup.issue_id)["Price History"], timeout=timeout)
    if result.ok:
        for table in result.tables:
            table["price_source"] = "mirror"
    return result, lookup


def _persist_stock_mapping(code: str, lookup: IssueLookup, stock_name: str = "") -> None:
    if lookup.status == "success" and lookup.issue_id:
        try:
            upsert_stock_map(code, lookup.issue_id, name=stock_name)
        except Exception:
            # The mapping cache is an optimization; never let it block the request.
            pass


def fetch_local_db_bundle(stock_code: str, issue_id: str = "", timeout: int = 30, mirror_status: str = "") -> SourceBundle:
    code = clean_stock_code(stock_code)
    resolved_issue_id = issue_id or issue_id_for_stock(code)
    lookup = IssueLookup(
        stock_code=code,
        issue_id=resolved_issue_id,
        method="cache" if load_stock_map(code).get("issue_id") else ("known mapping fallback" if issue_id_for_stock(code) else "local stock code"),
        status="success" if code and resolved_issue_id else "failed",
        message="Local CCASS snapshot DB selected; live HKEX SDW scraping is disabled.",
    )
    warnings: list[str] = []
    if not code:
        return SourceBundle(
            lookup=lookup,
            results={},
            metadata={"source": "local_db", "mirror_status": mirror_status, "mirror_base_url": mirror_base_url()},
            warnings=["Stock code is required for local DB mode."],
        )

    if not stock_fetched_today(code):
        warnings.append("Live HKEX SDW scraping is disabled; using existing local snapshot DB only.")

    if load_price_history(code).empty:
        price_result = fetch_yahoo_price_history(code, period_days=int(os.getenv("YAHOO_PRICE_PERIOD_DAYS", "90")))
        if price_result.ok:
            upsert_price_history(code, price_result.table, source="yahoo")
            warnings.append(price_result.warning)
        else:
            warnings.append(f"Yahoo price fetch failed: {price_result.error_type} - {price_result.error_message}")

    built = build_results_from_db(code, "local_db://ccass_snapshots")
    warnings.extend(built.warnings or [])
    if built.stock_name:
        lookup.message = f"{lookup.message} Stock name from local DB: {built.stock_name}"
    _persist_stock_mapping(code, lookup, built.stock_name)
    if built.history_depth_days <= 1 and built.latest_date:
        warnings.append(f"History limited to local snapshots since {built.latest_date}; mirror historical data unavailable")
    return SourceBundle(
        lookup=lookup,
        results=built.results,
        metadata={
            "source": "local_db",
            "mirror_status": mirror_status or "not_used",
            "mirror_base_url": mirror_base_url(),
            "history_depth_days": built.history_depth_days,
            **db_restore_status(),
        },
        warnings=warnings,
    )


def fetch_hybrid_bundle(stock_code: str, timeout: int = 30, headless: bool = True) -> SourceBundle:
    """Use local snapshots as a fallback, plus public Webb pages when available.

    A local snapshot database does not contain a Webb issue ID for every stock.
    Resolve that ID from the public orgdata page before deciding that the
    Webb-only history is unavailable.  This path deliberately uses requests
    only: a failed Webb page leaves the local/Yahoo result intact rather than
    requiring a browser runtime.
    """
    bundle = fetch_local_db_bundle(stock_code, timeout=timeout, mirror_status="hybrid")
    issue_id = bundle.lookup.issue_id
    if not issue_id:
        resolved = resolve_issue_id(stock_code, timeout=timeout, headless=headless)
        if resolved.status == "success" and resolved.issue_id:
            bundle.lookup = resolved
            issue_id = resolved.issue_id
            if resolved.result is not None:
                bundle.results["Company / orgdata"] = resolved.result
        else:
            if resolved.result is not None:
                bundle.results["Company / orgdata"] = resolved.result
            bundle.warnings.append(
                "Webb-site issue ID could not be resolved from the public orgdata page; "
                "continuing with local snapshot data only."
            )
            return bundle

    urls = issue_urls(issue_id)
    for section in ("Holdings", "Changes", "Big Changes", "Concentration", "Price History"):
        debug_kwargs = {}
        if os.getenv("CCASS_DEBUG_DUMP", "").strip().lower() in {"1", "true", "yes", "on"}:
            debug_kwargs["debug_stock"] = stock_code
        result = fetch_with_requests(section, urls[section], timeout=timeout, **debug_kwargs)
        local_result = bundle.results.get(section)
        if result.ok:
            # A successful remote refresh is newer and therefore wins over a
            # local snapshot; a failed refresh must never replace usable data.
            bundle.results[section] = result
        elif local_result is None or not local_result.ok:
            bundle.results[section] = result
        else:
            attempted_sources = getattr(local_result, "attempted_sources", None)
            if attempted_sources is None:
                attempted_sources = []
                try:
                    local_result.attempted_sources = attempted_sources
                except AttributeError:
                    pass
            attempted_sources.append(
                {
                    "source": "mirror",
                    "url": result.url,
                    "final_url": result.final_url,
                    "ok": False,
                    "status_code": result.status,
                    "error_type": result.error_type,
                    "error_message": result.error_message,
                }
            )
        if result.ok:
            if section == "Price History":
                for table in result.tables:
                    table["price_source"] = "mirror"
        else:
            bundle.warnings.append(
                f"Webb-site {section} fetch failed: {result.error_type} - {result.error_message}. "
                "Using available local fallback data for this section."
            )
    bundle.metadata.update(
        {
            "source": "hybrid_local_db_webb",
            "mirror_status": "direct_pages_only",
            "mirror_base_url": mirror_base_url(),
        }
    )
    return bundle


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
    if price_result and price_result.ok:
        for table in price_result.tables:
            table["price_source"] = "mirror"
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
    if os.getenv("CCASS_RENDER_FULL", "").strip().lower() in {"1", "true", "yes"}:
        render_bundle = fetch_render_api_bundle(code, timeout=timeout)
        if render_bundle is not None:
            return render_bundle
    issue_id = issue_id_for_stock(code)
    if mode in {"sdw", "local_db"}:
        local_bundle = fetch_local_db_bundle(code, issue_id=issue_id, timeout=timeout, mirror_status="disabled_by_config")
        if local_bundle.lookup.issue_id and local_bundle.results:
            return local_bundle
        hybrid_bundle = fetch_hybrid_bundle(code, timeout=timeout, headless=headless)
        if hybrid_bundle.lookup.issue_id or hybrid_bundle.results:
            hybrid_bundle.warnings.append("Local snapshot was empty; upstream fallback was used for this request.")
            return hybrid_bundle
        return local_bundle
    if mode == "mirror":
        return fetch_mirror_bundle(code, issue_id="", timeout=timeout, headless=headless, mirror_status="forced")
    return fetch_hybrid_bundle(code, timeout=timeout, headless=headless)
