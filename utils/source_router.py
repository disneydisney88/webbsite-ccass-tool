from __future__ import annotations

import json
import os
import requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    extract_issue_id_from_html,
    issue_urls,
    mirror_base_url,
    orgdata_url,
    resolve_issue_id_from_stock,
)
from .snapshot_db import (
    DB_PATH,
    build_results_from_db,
    db_restore_status,
    history_depth_days,
    load_mirror_probe,
    load_price_history,
    load_stock_map,
    stock_fetched_today,
    upsert_mirror_probe,
    upsert_price_history,
    upsert_stock_map,
)


CONFIG_PATH = Path(os.getenv("CCASS_SOURCE_CONFIG", "ccass_source_config.json"))
MIRROR_BROWSER_SECTIONS = {"Holdings", "Changes"}
PROBE_CACHE_PATH = Path(os.getenv("CCASS_MIRROR_PROBE_CACHE", "data/mirror_probe_status.json"))
VALID_MODES = {"auto", "mirror", "local_db", "hybrid_light", "sdw"}
RENDER_API_DEFAULT = "https://webbsite-ccass-api.onrender.com"
LOCAL_DATA_SECTIONS = ("Holdings", "Concentration", "Changes", "Big Changes", "Price History")


@dataclass
class SourceBundle:
    lookup: IssueLookup
    results: dict[str, FetchResult]
    metadata: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def has_real_ccass_data(results: dict[str, FetchResult]) -> bool:
    """Return whether results contain at least one usable CCASS data section.

    A stock-map or orgdata cache row is metadata only and must not make a
    local-only bundle look complete.
    """
    return any(
        (result := results.get(section)) is not None
        and result.ok
        and bool(result.tables)
        for section in LOCAL_DATA_SECTIONS
    )


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
    """Read the legacy probe file for backwards-compatible diagnostics."""
    if not PROBE_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(PROBE_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if data.get("date") == date.today().isoformat() and data.get("mirror_base_url") == mirror_base_url() else None


def _write_probe_cache(status: str, browser_sections: list[str] | None = None) -> None:
    PROBE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROBE_CACHE_PATH.write_text(
        json.dumps(
            {
                "date": date.today().isoformat(),
                "status": status,
                "mirror_base_url": mirror_base_url(),
                "browser_sections": browser_sections or [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def mirror_probe(stock_code: str, issue_id: str, timeout: int, headless: bool = True) -> str:
    cached = _probe_cache_today()
    if cached:
        return str(cached.get("status") or "unknown")
    url = issue_urls(issue_id).get("Holdings") if issue_id else orgdata_url(stock_code)
    result = fetch_with_requests("Mirror probe", url, timeout=max(3, min(timeout, 8)))
    browser_sections: list[str] = []
    if result.error_type == "JS_CHALLENGE" or (result.status == 200 and not result.ok):
        browser_sections = ["Holdings"]
    if browser_sections:
        browser_result = fetch_with_playwright("Mirror probe", url, timeout=max(10, min(timeout, 45)), headless=headless)
        status = "ok" if browser_result.ok else "failed"
    elif result.ok:
        status = "ok"
    else:
        status = "failed"
    _write_probe_cache(status, browser_sections)
    return status


def _browser_fallback_enabled() -> bool:
    return os.getenv("CCASS_BROWSER_FALLBACK", "on").strip().lower() not in {"0", "false", "no", "off"}


def _daily_mirror_probe(stock_code: str, issue_id: str, timeout: int, headless: bool = True) -> dict[str, object]:
    """Probe the two mirror pages once per day and persist the browser requirement."""
    probe_date = date.today().isoformat()
    base_url = mirror_base_url()
    cached = load_mirror_probe(probe_date, base_url, path=DB_PATH)
    if cached is not None:
        return cached

    urls = issue_urls(issue_id) if issue_id else {"Holdings": orgdata_url(stock_code)}
    browser_sections: list[str] = []
    failures: list[str] = []
    probe_results: dict[str, FetchResult] = {}
    checked_sections = [section for section in MIRROR_BROWSER_SECTIONS if section in urls]
    for section in checked_sections:
        result = fetch_with_requests("Mirror probe", urls[section], timeout=max(3, min(timeout, 8)))
        probe_results[section] = result
        if result.ok:
            continue
        if result.error_type == "JS_CHALLENGE":
            browser_sections.append(section)
        else:
            failures.append(f"{section}: {result.error_type or 'probe failed'}")

    if browser_sections:
        status = "browser_required"
    elif failures:
        status = "failed"
    else:
        status = "ok"
    error_message = "; ".join(failures)
    record = {
        "date": probe_date,
        "mirror_base_url": base_url,
        "status": status,
        "browser_sections": browser_sections,
        "error_message": error_message,
        "_results": probe_results,
    }
    upsert_mirror_probe(
        probe_date,
        base_url,
        status,
        browser_sections=browser_sections,
        error_message=error_message,
        path=DB_PATH,
    )
    return record


def _browser_fallback_result(
    section: str,
    request_result: FetchResult,
    timeout: int,
    headless: bool,
    stock_code: str,
) -> FetchResult:
    request_attempt = {
        "source": "requests",
        "url": request_result.url,
        "final_url": request_result.final_url,
        "ok": False,
        "status_code": request_result.status,
        "error_type": request_result.error_type,
        "error_message": request_result.error_message,
    }
    browser_result = fetch_with_playwright(
        section,
        request_result.url,
        timeout=timeout,
        headless=headless,
        debug_stock=stock_code,
    )
    browser_result.attempted_sources.append(request_attempt)
    if browser_result.ok:
        browser_result.method = "playwright_after_challenge"
        browser_result.fallback_method_used = "requests -> playwright_after_challenge"
        return browser_result

    detail = browser_result.error_message or "unknown browser error"
    unavailable = browser_result.error_type in {"ImportError", "CHROMIUM_UNAVAILABLE"} or "unavailable" in detail.lower() or "not installed" in detail.lower()
    prefix = "browser fallback unavailable in this environment" if unavailable else "browser fallback attempted and failed"
    if unavailable:
        request_result.error_type = "CHROMIUM_UNAVAILABLE"
        request_result.error_message = (
            "瀏覽器引擎不可用，已改用靜態抓取；靜態抓取返回 JS challenge。"
            f" {prefix}: {detail}"
        )
    else:
        request_result.error_message = f"{request_result.error_message}; {prefix}: {detail}"
    request_result.fallback_method_used = prefix
    request_result.attempted_sources.insert(0, request_attempt)
    request_result.attempted_sources.append(
        {
            "source": "playwright",
            "url": request_result.url,
            "final_url": browser_result.final_url,
            "ok": False,
            "status_code": browser_result.status,
            "error_type": browser_result.error_type,
            "error_message": detail,
        }
    )
    return request_result


def _describe_browser_failure(result: FetchResult) -> FetchResult:
    detail = result.error_message or "unknown browser error"
    unavailable = result.error_type in {"ImportError", "CHROMIUM_UNAVAILABLE"} or "unavailable" in detail.lower() or "not installed" in detail.lower()
    prefix = "browser fallback unavailable in this environment" if unavailable else "browser fallback attempted and failed"
    if unavailable:
        result.error_type = "CHROMIUM_UNAVAILABLE"
    result.fallback_method_used = prefix
    result.error_message = f"{prefix}: {detail}"
    return result


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
    cached_mapping = load_stock_map(code)
    resolved_issue_id = issue_id or str(cached_mapping.get("issue_id") or "")
    lookup_result = None
    if resolved_issue_id:
        lookup_result = FetchResult(
            name="Company / orgdata",
            url=f"local_db://stock_map/{code}",
            final_url=f"local_db://stock_map/{code}",
            method="cache",
            ok=True,
            status=200,
            tables=[pd.DataFrame([{"Code": code, "Name": cached_mapping.get("name", "")}])],
        )
    lookup = IssueLookup(
        stock_code=code,
        issue_id=resolved_issue_id,
        method="cache" if cached_mapping.get("issue_id") else ("known mapping fallback" if issue_id_for_stock(code) else "local stock code"),
        status="success" if code and resolved_issue_id else "failed",
        message="Local CCASS snapshot DB selected; live HKEX SDW scraping is disabled.",
        result=lookup_result,
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
    local_results = dict(built.results)
    if lookup_result is not None and "Company / orgdata" not in local_results:
        local_results["Company / orgdata"] = lookup_result
    return SourceBundle(
        lookup=lookup,
        results=local_results,
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
    Webb-only history is unavailable. A JavaScript challenge on the two
    browser-required pages may use the normal Playwright fallback.
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
    probe = _daily_mirror_probe(stock_code, issue_id, timeout=timeout, headless=headless)
    browser_enabled = _browser_fallback_enabled()
    if probe.get("status") == "failed":
        bundle.warnings.append(
            "Mirror daily probe failed; using requests first and challenge-only browser fallback. "
            f"{probe.get('error_message') or 'No probe detail available.'}"
        )
    if not browser_enabled:
        bundle.warnings.append("CCASS_BROWSER_FALLBACK is disabled; mirror JS challenges will remain unavailable.")
    probe_results = probe.get("_results") or {}
    for section in ("Holdings", "Changes", "Big Changes", "Concentration", "Price History"):
        debug_kwargs = {}
        if os.getenv("CCASS_DEBUG_DUMP", "").strip().lower() in {"1", "true", "yes", "on"}:
            debug_kwargs["debug_stock"] = stock_code
        if section in probe_results:
            result = probe_results[section]
            result.name = section
        else:
            result = fetch_with_requests(section, urls[section], timeout=timeout, **debug_kwargs)
        if browser_enabled and section in MIRROR_BROWSER_SECTIONS and result.error_type == "JS_CHALLENGE":
            result = _browser_fallback_result(section, result, timeout=timeout, headless=headless, stock_code=stock_code)
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
            attempted_sources.extend(getattr(result, "attempted_sources", []) or [])
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


def fetch_hybrid_light_bundle(
    stock_code: str,
    timeout: int = 30,
    include_price_history: bool = False,
) -> SourceBundle:
    """Use local data plus requests-only Webb sections, without Playwright."""
    code = clean_stock_code(stock_code)
    bundle = fetch_local_db_bundle(code, timeout=timeout, mirror_status="hybrid_light")
    issue_id = bundle.lookup.issue_id

    if not issue_id:
        org_result = fetch_with_requests("Company / orgdata", orgdata_url(code), timeout=timeout)
        issue_id, method = extract_issue_id_from_html(org_result.html)
        if issue_id:
            bundle.lookup = IssueLookup(
                stock_code=code,
                issue_id=issue_id,
                method=method or "extracted from orgdata",
                status="success",
                message="Issue ID resolved from Webb orgdata via requests.",
                result=org_result,
            )
            bundle.results["Company / orgdata"] = org_result
            try:
                upsert_stock_map(code, issue_id, name="")
            except Exception:
                pass
        else:
            bundle.lookup = IssueLookup(
                stock_code=code,
                issue_id="",
                method="requests",
                status="failed",
                message=org_result.error_message or "Unable to resolve Webb-site issue ID.",
                result=org_result,
            )
            bundle.results["Company / orgdata"] = org_result
            return bundle
    elif "Company / orgdata" not in bundle.results:
        bundle.results["Company / orgdata"] = bundle.lookup.result

    urls = issue_urls(issue_id)
    skipped_message = (
        "Requires browser; use source_preference='auto' via HTTP API "
        "(MCP wall-clock budget too short)"
    )
    for section in ("Holdings", "Changes"):
        local_result = bundle.results.get(section)
        if local_result is not None and local_result.ok and local_result.tables:
            continue
        bundle.results[section] = FetchResult(
            name=section,
            url=urls[section],
            final_url=urls[section],
            method="skipped",
            ok=False,
            skipped=True,
            error_type="BROWSER_REQUIRED",
            error_message=skipped_message,
        )

    request_sections = ["Big Changes", "Concentration"]
    if include_price_history:
        request_sections.append("Price History")
    else:
        local_price = bundle.results.get("Price History")
        if local_price is None or not local_price.ok or not local_price.tables:
            price_url = urls["Price History"]
            bundle.results["Price History"] = FetchResult(
                name="Price History",
                url=price_url,
                final_url=price_url,
                method="skipped",
                ok=False,
                skipped=True,
                error_type="NOT_REQUESTED",
                error_message=(
                    "Not fetched in hybrid_light; use get_webbsite_price_history"
                ),
            )

    pending_sections = []
    for section in request_sections:
        local_result = bundle.results.get(section)
        if local_result is not None and local_result.ok and local_result.tables:
            continue
        pending_sections.append(section)

    fetched_results: dict[str, FetchResult] = {}
    if pending_sections:
        with ThreadPoolExecutor(max_workers=min(2, len(pending_sections))) as executor:
            futures = {
                executor.submit(fetch_with_requests, section, urls[section], timeout): section
                for section in pending_sections
            }
            for future in as_completed(futures):
                section = futures[future]
                try:
                    fetched_results[section] = future.result()
                except Exception as exc:  # pragma: no cover - fetcher normally returns failures
                    fetched_results[section] = FetchResult(
                        name=section,
                        url=urls[section],
                        final_url=urls[section],
                        method="requests",
                        ok=False,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )

    for section in pending_sections:
        result = fetched_results[section]
        if result.ok and section == "Price History":
            for table in result.tables:
                table["price_source"] = "mirror"
        bundle.results[section] = result

    bundle.metadata.update(
        {
            "source": "hybrid_light",
            "mirror_status": "requests_only",
            "mirror_base_url": mirror_base_url(),
            "browser_sections_skipped": ["Holdings", "Changes"],
            "include_price_history": include_price_history,
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
        if local_bundle.lookup.issue_id and has_real_ccass_data(local_bundle.results):
            return local_bundle
        hybrid_bundle = fetch_hybrid_bundle(code, timeout=timeout, headless=headless)
        if hybrid_bundle.lookup.issue_id or hybrid_bundle.results:
            hybrid_bundle.warnings.append("Local snapshot was empty; upstream fallback was used for this request.")
            return hybrid_bundle
        return local_bundle
    if mode == "mirror":
        return fetch_mirror_bundle(code, issue_id="", timeout=timeout, headless=headless, mirror_status="forced")
    if mode == "hybrid_light":
        return fetch_hybrid_light_bundle(code, timeout=timeout)
    return fetch_hybrid_bundle(code, timeout=timeout, headless=headless)
