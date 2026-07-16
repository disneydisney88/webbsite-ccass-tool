from __future__ import annotations

import copy
import logging
import os
import re
import secrets
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Annotated, Any

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings
from pydantic import BaseModel, Field

from utils.exporters import parsed_to_json_ready
from utils.fetcher import (
    FetchResult,
    IssueLookup,
    clean_stock_code,
    fetch_all,
    fetch_with_requests,
    issue_urls,
    orgdata_url,
    resolve_issue_id_from_stock,
)
from utils.events import events_url, parse_events_html, parse_events_name
from utils.hkexnews import fetch_announcements
from utils.officers import (
    extract_org_id_from_html,
    officers_url,
    parse_officers_html,
    parse_officers_name,
    parse_shutdown_notice,
)
from utils.parser import parse_date_value, parse_results, to_number


API_TITLE = "Webb-site CCASS Research API"
API_VERSION = "1.6.0"
CACHE_TTL_SECONDS = 600
DEFAULT_API_BASE_URL = "https://webbsite-ccass-api.onrender.com"
SECTION_NAMES = ["Holdings", "Changes", "Big Changes", "Concentration", "Price History"]
# Fetch the CCASS sections concurrently so a slow or late section (notably
# "Price History", which is always last) cannot be starved of the shared
# timeout budget by the sections ahead of it. One worker per section.
MAX_FETCH_WORKERS = max(1, int(os.getenv("FETCH_MAX_WORKERS", str(len(SECTION_NAMES)))))

logger = logging.getLogger("webbsite_ccass_api")
bearer_scheme = HTTPBearer(auto_error=False)
_stock_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_mcp_session_context: Any = None

app = FastAPI(
    title=API_TITLE,
    version=API_VERSION,
    description="Read-only compact API for Webb-site CCASS research-ready summaries.",
    servers=[{"url": os.getenv("API_BASE_URL", DEFAULT_API_BASE_URL)}],
)

mcp_server = FastMCP(
    "Webb-site CCASS Research Server",
    instructions=(
        "Use get_ccass_stock_data to retrieve Hong Kong stock CCASS holdings, changes, "
        "big changes, concentration, fetch status and data quality warnings."
    ),
    stateless_http=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "webbsite-ccass-api.onrender.com",
            "localhost:*",
            "127.0.0.1:*",
            "[::1]:*",
        ],
        allowed_origins=[
            "https://claude.ai",
            "https://www.claude.ai",
            "http://localhost:*",
            "http://127.0.0.1:*",
            "http://[::1]:*",
        ],
    ),
)


class RootResponse(BaseModel):
    ok: bool
    service: str
    version: str
    links: dict[str, str]


class HealthResponse(BaseModel):
    ok: bool
    service: str
    version: str


class StockMetadata(BaseModel):
    code: str
    name: str
    issue_id: str
    holdings_date: str
    changes_date: str


class HoldingsSummary(BaseModel):
    total_in_ccass: str
    total_in_ccass_pct: str
    securities_not_in_ccass: str
    largest_participant: str
    holdings_total_count: int = Field(ge=0)
    holdings_returned_count: int = Field(ge=0)
    changes_total_count: int = Field(ge=0)
    changes_returned_count: int = Field(ge=0)
    big_changes_total_count: int = Field(ge=0)
    big_changes_returned_count: int = Field(ge=0)
    concentration_total_count: int = Field(ge=0)
    concentration_returned_count: int = Field(ge=0)
    truncated: bool


class ConcentrationSummary(BaseModel):
    top5_pct: str
    top10_pct: str
    latest_date: str
    # Dual-basis concentration for the latest date. *_of_ccass is the source
    # page's basis (% of shares in CCASS); *_of_issued is the same holding as a
    # percentage of total issued shares (matches Holdings cumulative %).
    top5_pct_of_ccass: float | None = None
    top10_pct_of_ccass: float | None = None
    top5_pct_of_issued: float | None = None
    top10_pct_of_issued: float | None = None
    issued_shares: str = ""
    issued_shares_as_of: str = ""
    issued_shares_may_be_stale: bool = False
    records: list[dict[str, Any]]


class StockCompactResponse(BaseModel):
    metadata: StockMetadata
    holdings_summary: HoldingsSummary
    holdings: list[dict[str, Any]]
    changes: list[dict[str, Any]]
    big_changes: list[dict[str, Any]]
    concentration: ConcentrationSummary
    fetch_summary: list[dict[str, Any]]
    data_quality_warnings: list[str]


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and (pd.isna(value) or value in {float("inf"), float("-inf")}):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def mask_secret(value: str | None) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return f"{value[:1]}...{value[-1:]} (len={len(value)})"
    return f"{value[:4]}...{value[-4:]} (len={len(value)})"


def verify_api_token(
    request: Request,
    key: str | None = Query(None, description="Optional API token for URL-only clients."),
    api_token: str | None = Query(None, description="Alias for key; optional API token for URL-only clients."),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    expected_token = os.getenv("API_TOKEN", "")
    if not expected_token:
        return
    query_token = api_token or key
    if query_token and secrets.compare_digest(query_token, expected_token):
        return
    header_key = request.headers.get("X-API-Key", "")
    if header_key and secrets.compare_digest(header_key, expected_token):
        return
    if credentials and credentials.scheme.lower() == "bearer" and secrets.compare_digest(credentials.credentials, expected_token):
        return
    supplied_token = query_token or header_key or (credentials.credentials if credentials else "")
    logger.warning(
        "API auth rejected: expected=%s supplied=%s has_key=%s has_api_token=%s has_x_api_key=%s has_bearer=%s",
        mask_secret(expected_token),
        mask_secret(supplied_token),
        bool(key),
        bool(api_token),
        bool(header_key),
        bool(credentials),
    )
    raise unauthorized()


def verify_bearer_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> None:
    expected_token = os.getenv("API_TOKEN", "")
    if not credentials or credentials.scheme.lower() != "bearer" or not expected_token:
        raise unauthorized()
    if not secrets.compare_digest(credentials.credentials, expected_token):
        raise unauthorized()


def make_lookup_from_issue_id(issue_id: str, stock_code: str = "") -> IssueLookup:
    return IssueLookup(
        stock_code=stock_code,
        issue_id=issue_id,
        method="api issue_id parameter",
        status="success",
        message="Issue ID was provided by API caller.",
    )


def cache_get(stock_code: str) -> dict[str, Any] | None:
    cached = _stock_cache.get(stock_code)
    if not cached:
        return None
    cached_at, payload = cached
    if time.monotonic() - cached_at > CACHE_TTL_SECONDS:
        _stock_cache.pop(stock_code, None)
        return None
    return copy.deepcopy(payload)


def cache_set(stock_code: str, payload: dict[str, Any]) -> None:
    _stock_cache[stock_code] = (time.monotonic(), copy.deepcopy(payload))


def remaining_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def short_section_timeout(deadline: float, requested_timeout: int) -> int:
    remaining = remaining_seconds(deadline)
    if remaining <= 1:
        return 1
    return max(1, min(4, requested_timeout, int(remaining)))


def parallel_section_timeout(deadline: float, requested_timeout: int) -> int:
    # Sections are fetched concurrently, so each may use up to the whole
    # remaining budget rather than a small per-section slice.
    remaining = remaining_seconds(deadline)
    if remaining <= 1:
        return 1
    return max(1, min(requested_timeout, int(remaining)))


def failed_result(name: str, url: str, message: str) -> FetchResult:
    return FetchResult(
        name=name,
        url=url,
        fetched_time="",
        method="api deadline",
        ok=False,
        error_type="TimeoutBudgetExceeded",
        error_message=message,
    )


def resolve_lookup(stock_code: str, timeout: int, deadline: float, headless: bool) -> IssueLookup:
    lookup_timeout = short_section_timeout(deadline, min(timeout, 8))
    if lookup_timeout <= 1:
        return IssueLookup(stock_code=stock_code, status="failed", message="Timeout budget exhausted before issue lookup.")
    return resolve_issue_id_from_stock(stock_code, timeout=lookup_timeout, headless=headless)


def fetch_compact_results(
    issue_id: str,
    stock_code: str,
    lookup: IssueLookup,
    timeout: int,
    deadline: float,
    fetch_fn: Callable[[str, str, int], FetchResult] = fetch_with_requests,
) -> dict[str, FetchResult]:
    results: dict[str, FetchResult] = {}
    if lookup.result:
        results["Company / orgdata"] = lookup.result
    elif stock_code:
        results["Company / orgdata"] = failed_result(
            "Company / orgdata",
            orgdata_url(stock_code),
            "Company lookup result was unavailable.",
        )

    section_targets = [(name, url) for name, url in issue_urls(issue_id).items() if name in SECTION_NAMES]

    # No budget left: fail every section uniformly instead of fetching none in
    # order (which used to starve whichever section came last).
    if remaining_seconds(deadline) <= 1:
        for name, url in section_targets:
            results[name] = failed_result(name, url, "Timeout budget exhausted before this section was fetched.")
        return results

    section_timeout = parallel_section_timeout(deadline, timeout)
    max_workers = min(len(section_targets), MAX_FETCH_WORKERS)
    section_results: dict[str, FetchResult] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(fetch_fn, name, url, section_timeout): (name, url)
            for name, url in section_targets
        }
        for future in as_completed(future_map):
            name, url = future_map[future]
            try:
                section_results[name] = future.result()
            except Exception as exc:  # pragma: no cover - fetch_fn already traps its own errors
                section_results[name] = failed_result(name, url, f"Section fetch raised {type(exc).__name__}: {exc}")

    # Re-assemble in canonical order so fetch_summary stays stable for callers.
    for name, url in section_targets:
        results[name] = section_results.get(name) or failed_result(name, url, "Section fetch produced no result.")
    return results


def build_base_payload(stock_code: str, timeout: int, headless: bool = True) -> dict[str, Any]:
    stock_code = clean_stock_code(stock_code)
    if not stock_code:
        raise HTTPException(status_code=400, detail="Provide stock_code.")

    cached = cache_get(stock_code)
    if cached:
        return cached

    deadline = time.monotonic() + timeout
    lookup = resolve_lookup(stock_code, timeout=timeout, deadline=deadline, headless=headless)
    warnings = []
    if lookup.status != "success" or not lookup.issue_id:
        warning = lookup.message or "Could not resolve Webb-site issue ID within the API timeout budget."
        payload = minimal_base_payload(stock_code=stock_code, issue_id="", warnings=[f"Issue lookup failed: {warning}"])
        cache_set(stock_code, payload)
        return payload

    results = fetch_compact_results(lookup.issue_id, stock_code, lookup, timeout=timeout, deadline=deadline)
    try:
        parsed = parse_results(
            lookup.issue_id,
            results,
            stock_code=lookup.stock_code or stock_code,
            id_lookup_method=lookup.method,
            id_lookup_status=lookup.status,
        )
        exported = parsed_to_json_ready(parsed, results)
    except Exception as exc:
        warnings.append(f"Parsing failed: {type(exc).__name__}: {exc}")
        exported = minimal_exported_payload(stock_code=stock_code, issue_id=lookup.issue_id, warnings=warnings, results=results)

    payload = {"exported": exported, "issue_id": lookup.issue_id}
    cache_set(stock_code, payload)
    return copy.deepcopy(payload)


def minimal_base_payload(stock_code: str, issue_id: str, warnings: list[str]) -> dict[str, Any]:
    return {"exported": minimal_exported_payload(stock_code, issue_id, warnings, {}), "issue_id": issue_id}


def minimal_exported_payload(
    stock_code: str,
    issue_id: str,
    warnings: list[str],
    results: dict[str, FetchResult],
) -> dict[str, Any]:
    return {
        "metadata": {
            "stock_code": stock_code,
            "stock_name": "",
            "issue_id": issue_id,
            "holdings_data_date": "",
            "changes_trading_date": "",
            "total_in_ccass_pct": "",
            "top5_cumulative_pct": "",
            "top10_cumulative_pct": "",
            "largest_participant": "",
        },
        "holdings": [],
        "changes": [],
        "bigchanges": [],
        "concentration": [],
        "fetch_summary": [result.to_log() for result in results.values()],
        "analysis_warnings": warnings,
    }


def numeric_value(record: dict[str, Any], keys: list[str]) -> float:
    for key in keys:
        if key in record:
            value = to_number(record.get(key))
            if value is not None:
                return value
    return float("-inf")


def date_value(record: dict[str, Any], keys: list[str]) -> pd.Timestamp:
    for key in keys:
        if key in record:
            value = parse_date_value(record.get(key))
            if value is not None:
                return value
    return pd.Timestamp.min


def compact_records(records: list[dict[str, Any]], section: str, limit: int) -> list[dict[str, Any]]:
    cleaned = [record for record in records if isinstance(record, dict)]
    if section == "holdings":
        sorted_records = sorted(
            cleaned,
            key=lambda item: numeric_value(item, ["Holding", "holding", "holding_percent", "Stake %", "stake_percent"]),
            reverse=True,
        )
    elif section == "changes":
        sorted_records = sorted(
            cleaned,
            key=lambda item: abs(numeric_value(item, ["Change", "change"])),
            reverse=True,
        )
    else:
        sorted_records = sorted(cleaned, key=lambda item: date_value(item, ["Date", "date", "Raw Date"]), reverse=True)
    return sorted_records[:limit]


def ratio_percent(numerator: Any, denominator: Any) -> float | None:
    top = to_number(numerator)
    bottom = to_number(denominator)
    if top is None or bottom is None or bottom == 0:
        return None
    return round(top / bottom * 100, 4)


def market_cap_value(close: Any, issued_securities: Any) -> float | None:
    close_value = to_number(close)
    shares = to_number(issued_securities)
    if close_value is None or shares is None:
        return None
    return round(close_value * shares, 2)


def enrich_price_history(records: list[dict[str, Any]], issued_securities: Any) -> list[dict[str, Any]]:
    enriched = []
    for record in records:
        item = dict(record)
        cap = market_cap_value(item.get("Close"), issued_securities)
        if cap is not None:
            item["Market Cap"] = cap
            turnover_ratio = ratio_percent(item.get("Turnover"), cap)
            if turnover_ratio is not None:
                item["Turnover / Market Cap %"] = turnover_ratio
        enriched.append(item)
    return enriched


def announcement_event_tags(row: dict[str, Any]) -> list[str]:
    text = f"{row.get('Category', '')} {row.get('Title', '')}".lower()
    patterns = [
        ("share_consolidation", r"合股|股份合併|share consolidation|consolidation of shares|consolidat(?:e|ion)"),
        ("share_subdivision", r"拆股|subdivision|share split|split of shares"),
        ("rights_issue", r"供股|rights issue"),
        ("open_offer", r"公開發售|open offer"),
        ("placing", r"配售|placing|subscription"),
        ("general_offer", r"全面要約|general offer|mandatory unconditional cash offer|go "),
        ("inside_information", r"內幕消息|inside information"),
        ("change_company_name", r"更改公司名稱|change of company name|change.*name"),
        ("board_change", r"董事|director|board"),
        ("trading_halt", r"停牌|暫停買賣|trading halt|suspension"),
        ("resumption", r"復牌|恢復買賣|resumption"),
        ("capital_reorganisation", r"股本重組|capital reorganisation|capital restructuring"),
    ]
    return [tag for tag, pattern in patterns if re.search(pattern, text, flags=re.I)]


def build_participant_id_map(holdings: list[dict[str, Any]]) -> dict[str, str]:
    """Map participant display name -> CCASS ID using the Holdings table.

    The Big Changes source page lists participants by name only, so this is the
    only place a CCASS ID can be recovered for a big-change row.
    """
    mapping: dict[str, str] = {}
    for row in holdings:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Participant") or "").strip()
        ccass_id = row.get("CCASS ID") or row.get("ccass_id") or ""
        ccass_id = str(ccass_id).strip()
        if name and ccass_id and name not in mapping:
            mapping[name] = ccass_id
    return mapping


def enrich_big_changes(
    records: list[dict[str, Any]], participant_id_map: dict[str, str]
) -> list[dict[str, Any]]:
    """Add slim, explicitly-named fields to each big-change row.

    Existing keys (Date, Participant, Change %, Change in shares) are kept for
    backward compatibility; new snake_case keys are added alongside. participant_id
    is null when the name cannot be matched to a Holdings row (never fabricated).
    """
    enriched = []
    for record in records:
        if not isinstance(record, dict):
            continue
        item = dict(record)
        name = str(item.get("Participant") or "").strip()
        shares = item.get("Change in shares")
        item["participant_name"] = name or None
        item["participant_id"] = participant_id_map.get(name)
        item["change_shares"] = to_number(shares) if shares not in (None, "") else None
        item["change_pct"] = to_number(item.get("Change %"))
        enriched.append(item)
    return enriched


def enrich_concentration_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Add both concentration bases to each record and flag stale issued-share bases.

    The source page reports Top 5 / Top 10 as a percentage of shares *in CCASS*.
    Analysis usually also wants the percentage of *issued* shares, which equals
    the CCASS-basis figure scaled by "Stake in CCASS %". When a resulting
    of-issued figure exceeds 100% (typically after a placement/consolidation the
    old issued-share base no longer reflects), the record is flagged stale.
    """
    enriched: list[dict[str, Any]] = []
    any_stale = False
    for record in records:
        if not isinstance(record, dict):
            continue
        item = dict(record)
        top5_ccass = to_number(item.get("Top 5 %"))
        top10_ccass = to_number(item.get("Top 10 %"))
        stake = to_number(item.get("Stake in CCASS %"))
        top5_issued = round(top5_ccass * stake / 100, 4) if top5_ccass is not None and stake is not None else None
        top10_issued = round(top10_ccass * stake / 100, 4) if top10_ccass is not None and stake is not None else None
        item["top5_pct_of_ccass"] = top5_ccass
        item["top10_pct_of_ccass"] = top10_ccass
        item["top5_pct_of_issued"] = top5_issued
        item["top10_pct_of_issued"] = top10_issued
        stale = bool(
            (stake is not None and stake > 100)
            or (top5_issued is not None and top5_issued > 100)
            or (top10_issued is not None and top10_issued > 100)
        )
        item["issued_shares_may_be_stale"] = stale
        any_stale = any_stale or stale
        enriched.append(item)
    return enriched, any_stale


def section_failure_warnings(fetch_summary: list[dict[str, Any]]) -> list[str]:
    warnings = []
    for row in fetch_summary:
        section = row.get("Section") or row.get("section") or "Unknown section"
        status_text = str(row.get("Status") or row.get("ok") or "").lower()
        error = row.get("Error") or row.get("error_message") or ""
        failed = status_text in {"failed", "false"} or bool(error)
        if failed:
            warnings.append(f"{section} failed or incomplete: {error or status_text}")
    return warnings


def build_stock_payload(
    stock_code: str,
    timeout: int = 30,
    holdings_limit: int = 15,
    changes_limit: int = 20,
    big_changes_limit: int = 10,
    concentration_limit: int = 15,
    headless: bool = True,
) -> dict[str, Any]:
    base = build_base_payload(stock_code=stock_code, timeout=timeout, headless=headless)
    exported = base.get("exported", {})
    metadata = exported.get("metadata", {})

    holdings = exported.get("holdings", [])
    changes = exported.get("changes", [])
    big_changes = exported.get("bigchanges", [])
    concentration = exported.get("concentration", [])

    compact_holdings = compact_records(holdings, "holdings", holdings_limit)
    compact_changes = compact_records(changes, "changes", changes_limit)
    compact_big_changes = compact_records(big_changes, "big_changes", big_changes_limit)
    compact_concentration = compact_records(concentration, "concentration", concentration_limit)

    # Enrich big changes with participant_id (joined from Holdings) and slim,
    # explicitly-named fields; existing keys are preserved for compatibility.
    compact_big_changes = enrich_big_changes(compact_big_changes, build_participant_id_map(holdings))

    # Concentration dual-basis (of CCASS vs of issued) + stale-base detection.
    compact_concentration, concentration_stale = enrich_concentration_records(compact_concentration)
    latest_concentration = compact_concentration[0] if compact_concentration else {}

    fetch_summary = exported.get("fetch_summary", [])
    warnings = list(exported.get("analysis_warnings", []))
    warnings.extend(warning for warning in section_failure_warnings(fetch_summary) if warning not in warnings)
    if concentration_stale:
        stale_warning = (
            "Concentration % of issued shares exceeds 100% on at least one date; "
            "the issued-share base may be stale (not yet reflecting a recent "
            "placement/consolidation). issued_shares_may_be_stale is set."
        )
        if stale_warning not in warnings:
            warnings.append(stale_warning)

    truncated = any(
        [
            len(holdings) > len(compact_holdings),
            len(changes) > len(compact_changes),
            len(big_changes) > len(compact_big_changes),
            len(concentration) > len(compact_concentration),
        ]
    )

    return json_safe(
        {
            "metadata": {
                "code": metadata.get("stock_code", clean_stock_code(stock_code)),
                "name": metadata.get("stock_name", ""),
                "issue_id": metadata.get("issue_id", base.get("issue_id", "")),
                "holdings_date": metadata.get("holdings_data_date", ""),
                "changes_date": metadata.get("changes_trading_date", ""),
            },
            "holdings_summary": {
                "total_in_ccass": metadata.get("total_in_ccass", ""),
                "total_in_ccass_pct": metadata.get("total_in_ccass_pct", ""),
                "securities_not_in_ccass": metadata.get("securities_not_in_ccass", ""),
                "largest_participant": metadata.get("largest_participant", ""),
                "holdings_total_count": len(holdings),
                "holdings_returned_count": len(compact_holdings),
                "changes_total_count": len(changes),
                "changes_returned_count": len(compact_changes),
                "big_changes_total_count": len(big_changes),
                "big_changes_returned_count": len(compact_big_changes),
                "concentration_total_count": len(concentration),
                "concentration_returned_count": len(compact_concentration),
                "truncated": truncated,
            },
            "holdings": compact_holdings,
            "changes": compact_changes,
            "big_changes": compact_big_changes,
            "concentration": {
                "top5_pct": metadata.get("top5_cumulative_pct", ""),
                "top10_pct": metadata.get("top10_cumulative_pct", ""),
                "latest_date": metadata.get("concentration_latest_date", ""),
                "top5_pct_of_ccass": latest_concentration.get("top5_pct_of_ccass"),
                "top10_pct_of_ccass": latest_concentration.get("top10_pct_of_ccass"),
                "top5_pct_of_issued": latest_concentration.get("top5_pct_of_issued"),
                "top10_pct_of_issued": latest_concentration.get("top10_pct_of_issued"),
                "issued_shares": metadata.get("issued_securities", ""),
                "issued_shares_as_of": metadata.get("holdings_data_date", ""),
                "issued_shares_may_be_stale": concentration_stale,
                "records": compact_concentration,
            },
            "fetch_summary": fetch_summary,
            "data_quality_warnings": warnings,
        }
    )


def build_price_history_payload(stock_code: str, limit: int = 80, timeout: int = 30, headless: bool = True) -> dict[str, Any]:
    base = build_base_payload(stock_code=stock_code, timeout=timeout, headless=headless)
    exported = base.get("exported", {})
    metadata = exported.get("metadata", {})
    price_history = exported.get("price_history", [])
    issued_securities = metadata.get("issued_securities", "")
    compact_price_history = enrich_price_history(compact_records(price_history, "price_history", limit), issued_securities)
    fetch_summary = exported.get("fetch_summary", [])
    warnings = list(exported.get("analysis_warnings", []))
    warnings.extend(warning for warning in section_failure_warnings(fetch_summary) if warning not in warnings)
    price_fetch = [
        row
        for row in fetch_summary
        if str(row.get("Section") or row.get("section") or "").lower() == "price history"
    ]

    return json_safe(
        {
            "metadata": {
                "code": metadata.get("stock_code", clean_stock_code(stock_code)),
                "name": metadata.get("stock_name", ""),
                "issue_id": metadata.get("issue_id", base.get("issue_id", "")),
            },
            "price_summary": {
                "latest_date": metadata.get("price_history_latest_date", ""),
                "latest_close": metadata.get("latest_price", ""),
                "latest_volume": metadata.get("latest_price_volume", ""),
                "latest_turnover": metadata.get("latest_price_turnover", ""),
                "latest_vwap": metadata.get("latest_price_vwap", ""),
                "issued_securities": issued_securities,
                "latest_market_cap": market_cap_value(metadata.get("latest_price", ""), issued_securities),
                "latest_turnover_to_market_cap_pct": ratio_percent(
                    metadata.get("latest_price_turnover", ""),
                    market_cap_value(metadata.get("latest_price", ""), issued_securities),
                ),
                "price_history_total_count": len(price_history),
                "price_history_returned_count": len(compact_price_history),
                "truncated": len(price_history) > len(compact_price_history),
            },
            "price_history": compact_price_history,
            "fetch_summary": price_fetch,
            "data_quality_warnings": warnings,
        }
    )


def build_hkex_announcements_payload(stock_code: str, period_years: int = 1, limit: int = 100, timeout: int = 30) -> dict[str, Any]:
    result = fetch_announcements(
        stock_code=stock_code,
        period_years=period_years,
        timeout=timeout,
        row_range=limit,
    )
    rows = [] if result.table is None or result.table.empty else result.table.to_dict(orient="records")
    for row in rows:
        row["Event tags"] = announcement_event_tags(row)
    all_tags = sorted({tag for row in rows for tag in row.get("Event tags", [])})
    return json_safe(
        {
            "metadata": {
                "code": result.stock_code,
                "name": result.stock_name,
                "hkex_stock_id": result.hkex_stock_id,
                "from_date": result.from_date,
                "to_date": result.to_date,
                "period_years": result.period_years,
                "source_url": result.url,
            },
            "announcements_summary": {
                "ok": result.ok,
                "total_count": result.total_count,
                "returned_count": len(rows),
                "truncated": result.total_count > len(rows),
                "error": result.error,
                "event_tags_found": all_tags,
            },
            "announcements": rows,
            "data_quality_warnings": [result.error] if result.error else [],
        }
    )


def build_events_payload(stock_code: str, limit: int = 30, timeout: int = 30, headless: bool = True) -> dict[str, Any]:
    code = clean_stock_code(stock_code)
    if not code:
        raise HTTPException(status_code=400, detail="Provide code.")
    lookup = resolve_issue_id_from_stock(code, timeout=min(timeout, 12), headless=headless)
    warnings: list[str] = []
    if lookup.status != "success" or not lookup.issue_id:
        return json_safe(
            {
                "metadata": {"code": code, "name": "", "issue_id": "", "source_url": ""},
                "events_summary": {"total_count": 0, "returned_count": 0, "truncated": False},
                "events": [],
                "data_quality_warnings": [f"Issue lookup failed: {lookup.message or 'Webb-site issue ID not resolved.'}"],
            }
        )

    url = events_url(lookup.issue_id)
    result = fetch_with_requests("Events", url, timeout=min(timeout, 20))
    records: list[dict[str, Any]] = []
    name = ""
    if not result.html:
        warnings.append(f"Events fetch failed: {result.error_type or 'error'}: {result.error_message}")
    else:
        records = parse_events_html(result.html)
        name = parse_events_name(result.html)
        if not records:
            warnings.append("No events were found for this stock (source table missing or empty).")

    limited = records[:limit]
    return json_safe(
        {
            "metadata": {"code": code, "name": name, "issue_id": lookup.issue_id, "source_url": url},
            "events_summary": {
                "total_count": len(records),
                "returned_count": len(limited),
                "truncated": len(records) > len(limited),
            },
            "events": limited,
            "data_quality_warnings": warnings,
        }
    )


def build_officers_payload(
    stock_code: str, snapshot_date: str | None = None, timeout: int = 30, headless: bool = True
) -> dict[str, Any]:
    code = clean_stock_code(stock_code)
    if not code:
        raise HTTPException(status_code=400, detail="Provide code.")
    lookup = resolve_issue_id_from_stock(code, timeout=min(timeout, 12), headless=headless)
    warnings: list[str] = []
    org_id = extract_org_id_from_html(lookup.result.html if lookup.result else "")
    if not org_id:
        return json_safe(
            {
                "metadata": {"code": code, "name": "", "org_id": "", "source_url": ""},
                "officers_summary": {"total_count": 0, "current_count": 0, "snapshot_date": snapshot_date or ""},
                "officers": [],
                "data_quality_warnings": ["Could not resolve the Webb-site organisation id for this stock."],
            }
        )

    url = officers_url(org_id, snapshot_date)
    result = fetch_with_requests("Officers", url, timeout=min(timeout, 20))
    officers: list[dict[str, Any]] = []
    name = ""
    if not result.html:
        warnings.append(f"Officers fetch failed: {result.error_type or 'error'}: {result.error_message}")
    else:
        officers = parse_officers_html(result.html)
        name = parse_officers_name(result.html)
        notice = parse_shutdown_notice(result.html)
        if notice:
            warnings.append(f"Webb-site officer data notice: {notice}")
        if not officers:
            warnings.append("No officers were found for this stock (source table missing or empty).")

    current = [officer for officer in officers if officer.get("is_current")]
    return json_safe(
        {
            "metadata": {"code": code, "name": name, "org_id": org_id, "source_url": url},
            "officers_summary": {
                "total_count": len(officers),
                "current_count": len(current),
                "snapshot_date": snapshot_date or "",
            },
            "officers": officers,
            "data_quality_warnings": warnings,
        }
    )


@mcp_server.tool(
    name="get_ccass_stock_data",
    description=(
        "Fetch Webb-site CCASS research data for a Hong Kong listed stock. "
        "Returns metadata, holdings_summary, holdings, changes, big_changes, "
        "concentration, fetch_summary and data_quality_warnings as JSON."
    ),
)
async def get_ccass_stock_data(
    code: Annotated[str, Field(pattern=r"^[0-9]{5}$", description="Hong Kong stock code, e.g. 01592.")],
    holdings_limit: Annotated[int, Field(ge=1, le=50)] = 15,
    changes_limit: Annotated[int, Field(ge=1, le=50)] = 20,
    big_changes_limit: Annotated[int, Field(ge=1, le=50)] = 10,
    concentration_limit: Annotated[int, Field(ge=1, le=60)] = 15,
) -> dict[str, Any]:
    return build_stock_payload(
        stock_code=code,
        timeout=30,
        holdings_limit=holdings_limit,
        changes_limit=changes_limit,
        big_changes_limit=big_changes_limit,
        concentration_limit=concentration_limit,
        headless=True,
    )


@mcp_server.tool(
    name="get_webbsite_price_history",
    description=(
        "Fetch Webb-site price history for a Hong Kong listed stock. "
        "Returns latest close, volume, turnover, VWAP and recent price_history rows."
    ),
)
async def get_webbsite_price_history(
    code: Annotated[str, Field(pattern=r"^[0-9]{5}$", description="Hong Kong stock code, e.g. 03321.")],
    limit: Annotated[int, Field(ge=1, le=200)] = 80,
) -> dict[str, Any]:
    return build_price_history_payload(stock_code=code, limit=limit, timeout=30, headless=True)


@mcp_server.tool(
    name="get_hkex_announcements",
    description=(
        "Fetch HKEXnews announcement list for a Hong Kong listed stock. "
        "Returns publish time, category, title, file type, URL and news ID rows."
    ),
)
async def get_hkex_announcements(
    code: Annotated[str, Field(pattern=r"^[0-9]{5}$", description="Hong Kong stock code, e.g. 03321.")],
    period_years: Annotated[int, Field(ge=1, le=2)] = 1,
    limit: Annotated[int, Field(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    return build_hkex_announcements_payload(stock_code=code, period_years=period_years, limit=limit, timeout=30)


@mcp_server.tool(
    name="get_stock_events",
    description=(
        "Fetch Webb-site corporate events for a Hong Kong listed stock: dividends, "
        "splits/consolidations, bonus issues, rights and other capital actions, with "
        "announce/ex dates and new:old ratios."
    ),
)
async def get_stock_events(
    code: Annotated[str, Field(pattern=r"^[0-9]{5}$", description="Hong Kong stock code, e.g. 03321.")],
    limit: Annotated[int, Field(ge=1, le=200)] = 30,
) -> dict[str, Any]:
    return build_events_payload(stock_code=code, limit=limit, timeout=30, headless=True)


@mcp_server.tool(
    name="get_stock_officers",
    description=(
        "Fetch Webb-site directors and officers for a Hong Kong listed stock: name, "
        "sex, age, position (code and full title), appointment and resignation dates, "
        "and whether the person is currently serving."
    ),
)
async def get_stock_officers(
    code: Annotated[str, Field(pattern=r"^[0-9]{5}$", description="Hong Kong stock code, e.g. 03321.")],
    snapshot_date: Annotated[str | None, Field(description="Optional YYYY-MM-DD snapshot date.")] = None,
) -> dict[str, Any]:
    return build_officers_payload(stock_code=code, snapshot_date=snapshot_date, timeout=30, headless=True)


def build_full_stock_payload(stock_code: str, timeout: int = 60, headless: bool = True) -> dict[str, Any]:
    stock_code = clean_stock_code(stock_code)
    if not stock_code:
        raise HTTPException(status_code=400, detail="Provide stock_code.")
    lookup = resolve_issue_id_from_stock(stock_code, timeout=timeout, headless=headless)
    if lookup.status != "success" or not lookup.issue_id:
        raise HTTPException(status_code=502, detail=lookup.message or "Could not resolve Webb-site issue ID.")
    results = fetch_all(lookup.issue_id, stock_code=stock_code, timeout=timeout, headless=headless)
    parsed = parse_results(
        lookup.issue_id,
        results,
        stock_code=lookup.stock_code or stock_code,
        id_lookup_method=lookup.method,
        id_lookup_status=lookup.status,
    )
    return json_safe(parsed_to_json_ready(parsed, results))


@app.get("/", response_model=RootResponse)
def root() -> dict[str, Any]:
    return {
        "ok": True,
        "service": API_TITLE,
        "version": API_VERSION,
        "links": {"health": "/health", "openapi": "/openapi.json", "stock": "/api/stock"},
    }


@app.get("/health", response_model=HealthResponse)
def health() -> dict[str, Any]:
    return {"ok": True, "service": API_TITLE, "version": API_VERSION}


@app.on_event("startup")
async def start_mcp_session_manager() -> None:
    global _mcp_session_context
    logger.info("API auth config: API_TOKEN=%s", mask_secret(os.getenv("API_TOKEN", "")))
    _mcp_session_context = mcp_server.session_manager.run()
    await _mcp_session_context.__aenter__()


@app.on_event("shutdown")
async def stop_mcp_session_manager() -> None:
    global _mcp_session_context
    if _mcp_session_context is not None:
        await _mcp_session_context.__aexit__(None, None, None)
        _mcp_session_context = None


@app.get("/robots.txt", include_in_schema=False)
def robots_txt() -> Response:
    return Response(
        "User-agent: *\n"
        "Allow: /api/\n"
        "Allow: /mcp\n"
        "Allow: /health\n"
        "Allow: /openapi.json\n"
        "Disallow:\n",
        media_type="text/plain",
    )


@app.get("/api/stock", response_model=StockCompactResponse, dependencies=[Depends(verify_api_token)])
def get_stock(
    code: str | None = Query(None, description="HK stock code, e.g. 01592."),
    stock_code: str | None = Query(None, description="Backward-compatible alias for code."),
    timeout: int = Query(30, ge=10, le=35, description="Overall compact API timeout budget in seconds."),
    holdings_limit: int = Query(15, ge=1, le=50, description="Maximum holdings rows returned."),
    changes_limit: int = Query(20, ge=1, le=50, description="Maximum changes rows returned."),
    big_changes_limit: int = Query(10, ge=1, le=50, description="Maximum big changes rows returned."),
    concentration_limit: int = Query(15, ge=1, le=60, description="Maximum concentration rows returned."),
) -> dict[str, Any]:
    requested_code = code or stock_code or ""
    if not requested_code:
        raise HTTPException(status_code=400, detail="Provide code.")
    return build_stock_payload(
        stock_code=requested_code,
        timeout=timeout,
        holdings_limit=holdings_limit,
        changes_limit=changes_limit,
        big_changes_limit=big_changes_limit,
        concentration_limit=concentration_limit,
        headless=True,
    )


@app.get("/api/stock/events", dependencies=[Depends(verify_api_token)])
def get_stock_events_endpoint(
    code: str | None = Query(None, description="HK stock code, e.g. 03321."),
    stock_code: str | None = Query(None, description="Backward-compatible alias for code."),
    limit: int = Query(30, ge=1, le=200, description="Maximum event rows returned."),
    timeout: int = Query(30, ge=10, le=35, description="Fetch timeout budget in seconds."),
) -> dict[str, Any]:
    requested_code = code or stock_code or ""
    if not requested_code:
        raise HTTPException(status_code=400, detail="Provide code.")
    return build_events_payload(stock_code=requested_code, limit=limit, timeout=timeout, headless=True)


@app.get("/api/stock/officers", dependencies=[Depends(verify_api_token)])
def get_stock_officers_endpoint(
    code: str | None = Query(None, description="HK stock code, e.g. 03321."),
    stock_code: str | None = Query(None, description="Backward-compatible alias for code."),
    snapshot_date: str | None = Query(None, description="Optional YYYY-MM-DD snapshot date."),
    timeout: int = Query(30, ge=10, le=35, description="Fetch timeout budget in seconds."),
) -> dict[str, Any]:
    requested_code = code or stock_code or ""
    if not requested_code:
        raise HTTPException(status_code=400, detail="Provide code.")
    return build_officers_payload(
        stock_code=requested_code, snapshot_date=snapshot_date, timeout=timeout, headless=True
    )


@app.get("/api/stock/full", include_in_schema=False, dependencies=[Depends(verify_bearer_token)])
def get_stock_full(
    stock_code: str = Query(..., description="HK stock code, e.g. 01592."),
    timeout: int = Query(60, ge=10, le=120, description="Timeout per source page in seconds."),
) -> dict[str, Any]:
    return build_full_stock_payload(stock_code=stock_code, timeout=timeout, headless=True)


app.mount("/mcp", mcp_server.streamable_http_app())
