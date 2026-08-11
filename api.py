from __future__ import annotations

import asyncio
import copy
import logging
import os
import re
import secrets
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Empty, Queue
from threading import Thread
from typing import Annotated, Any

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field

from utils.date_semantics import (
    SECTION_DATE_BASIS,
    SETTLEMENT_NOTE,
    annotate_records,
    build_date_query_plan,
    derive_dates,
    normalize_date,
    unavailable_data_as_of,
)
from utils.exporters import parsed_to_json_ready
from utils.fetcher import (
    FetchResult,
    IssueLookup,
    clean_stock_code,
    fetch_all,
    fetch_page,
    fetch_with_requests,
    issue_urls,
    mirror_base_url,
    orgdata_url,
    resolve_issue_id_from_stock,
)
from utils.events import events_url, parse_events_html, parse_events_name
from utils.f10_equity import (
    f10_equity_url,
    latest_share_capital,
    parse_f10_buybacks,
    parse_f10_share_changes,
)
from utils.f10_managers import f10_managers_url, parse_f10_managers_html, parse_f10_stock_name
from utils.snapshot import diff_snapshots, parse_holdings_snapshot, snapshot_url
from utils.errors import errors_from_fetch_summary, errors_from_warnings, structured_error
from utils.hkexnews import fetch_announcements
from utils.hkex_announcement_pdf import fetch_announcement_pdf as fetch_hkex_announcement_pdf
from utils.officers import (
    extract_org_id_from_html,
    officers_url,
    parse_officers_html,
    parse_officers_name,
    parse_shutdown_notice,
)
from utils.participants import categorize
from utils.parser import parse_date_value, parse_results, to_number
from utils.source_router import fetch_local_db_bundle, fetch_mirror_bundle, fetch_source_bundle_for_stock, issue_id_for_stock
from utils.fetch_yahoo import fetch_yahoo_price_history
from utils.upstream_probe import probe_url, probe_upstreams
from utils.snapshot_db import (
    DB_PATH,
    db_restore_status,
    export_db_bytes,
    latest_snapshot_date,
    load_snapshot,
    load_stock_meta,
    load_watchlist_entries,
    restore_snapshot_db_from_backup,
    upsert_price_history,
)


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


API_TITLE = "Webb-site CCASS Research API"
API_SERVICE = "webbsite-ccass-api"
API_VERSION = "1.13.0"
CACHE_TTL_SECONDS = max(0, int_env("API_CACHE_TTL_SECONDS", 86400))
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
_APP_STARTED_MONOTONIC = time.monotonic()

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
    uptime_seconds: int
    upstreams: dict[str, Any] | None = None


class StockMetadata(BaseModel):
    code: str
    name: str
    issue_id: str
    holdings_date: str
    holdings_implied_trade_date: str = ""
    changes_date: str
    changes_implied_trade_date: str = ""
    big_changes_date: str = ""
    big_changes_implied_trade_date: str = ""
    concentration_date: str = ""
    concentration_implied_trade_date: str = ""
    data_as_of_trading_date: str = ""
    date_basis_by_section: dict[str, str] = Field(default_factory=dict)
    date_input_basis: str = "trade"
    changes_requested_from: str = ""
    changes_requested_to: str = ""
    changes_queried_settlement_from: str = ""
    changes_queried_settlement_to: str = ""
    big_changes_requested_from: str = ""
    big_changes_requested_to: str = ""
    big_changes_queried_settlement_from: str = ""
    big_changes_queried_settlement_to: str = ""
    settlement_note: str = ""
    source: str = ""
    mirror_status: str = ""
    mirror_base_url: str = ""
    history_depth_days: int = Field(default=0, ge=0)
    db_restored_from_backup: bool = False
    price_source: str = ""


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


_NUMISH = float | int | str | None


class RecordModel(BaseModel):
    """Base for source-table rows: declared fields are documented in the schema,
    but arbitrary source columns (e.g. "Stake %", "CCASS ID") still pass through."""

    model_config = ConfigDict(extra="allow")


class HoldingRecord(RecordModel):
    Participant: str | None = None
    category: str | None = None


class ChangeRecord(RecordModel):
    Participant: str | None = None
    category: str | None = None


class BigChangeRecord(RecordModel):
    participant_id: str | None = None
    participant_name: str | None = None
    change_shares: _NUMISH = None
    change_pct: float | str | None = None
    category: str | None = None


class ConcentrationRecord(RecordModel):
    top5_pct_of_ccass: float | str | None = None
    top10_pct_of_ccass: float | str | None = None
    top5_pct_of_issued: float | str | None = None
    top10_pct_of_issued: float | str | None = None
    issued_shares_may_be_stale: bool | None = None


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
    records: list[ConcentrationRecord]


class StockCompactResponse(BaseModel):
    ok: bool = True
    data_as_of: str = ""
    source: str = ""
    metadata: StockMetadata
    holdings_summary: HoldingsSummary
    holdings: list[HoldingRecord]
    changes: list[ChangeRecord]
    big_changes: list[BigChangeRecord]
    concentration: ConcentrationSummary
    price_history: list[RecordModel] = []
    fetch_summary: list[dict[str, Any]]
    data_quality_warnings: list[str]
    errors: list[dict[str, Any]] = []


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
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=structured_error("AUTH_FAILED", "Missing or invalid API token."),
    )


def collect_structured_errors(fetch_summary: list[dict[str, Any]], warnings: list[str]) -> list[dict[str, Any]]:
    """Merge fetch-summary failures and analysis warnings into deduped errors."""
    errors = errors_from_fetch_summary(fetch_summary) + errors_from_warnings(warnings)
    seen = set()
    deduped = []
    for error in errors:
        key = (error["error_code"], error["message"])
        if key not in seen:
            seen.add(key)
            deduped.append(error)
    return deduped


def with_source_fields(payload: dict[str, Any], data_as_of: Any = "", source: Any = "") -> dict[str, Any]:
    """Attach common provenance fields, filling blank values when available."""
    if not payload.get("data_as_of") and data_as_of:
        payload["data_as_of"] = data_as_of
    else:
        payload.setdefault("data_as_of", "")
    if not payload.get("source") and source:
        payload["source"] = source
    else:
        payload.setdefault("source", "")
    return payload


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
       ÛÞ»òÚ$z{-®éÜj×••’ÔÔÒÔDBâ"•ÒÀ¢FFUö#¢ææ÷FFVE·7G"Âf–VÆB‡GFW&ã×"%åÆG³GÒÕÆG³'ÒÕÆG³'ÒB"ÂFW67&—F–öãÒ$ÆFW"FFRÂ•••’ÔÔÒÔDBâ"•ÒÀ¢’ÓâF–7E·7G"Âç•Ó ¢&WGW&â'V–ÆEöF–fe÷–ÆöB‡7Fö6µö6öFSÖ6öFRÂFFUöÖFFUöÂFFUö#ÖFFUö"ÂF–ÖV÷WCÓ3  ¦FVb'V–ÆEögVÆÅ÷7Fö6µ÷–ÆöB‡7Fö6µö6öFS¢7G"Ò""Â—77VUö–C¢7G"Ò""ÂF–ÖV÷WC¢–çBÒcÂ†VFÆW73¢&ööÂÒG'VR’ÓâF–7E·7G"Âç•Ó ¢7Fö6µö6öFRÒ6ÆVå÷7Fö6µö6öFR‡7Fö6µö6öFR¢—77VUö–BÒ7G"†—77VUö–B÷"""’ç7G&—‚¢–bæ÷B7Fö6µö6öFRæBæ÷B—77VUö–C ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCÂFWF–ÃÒ%&÷f–FR7Fö6µö6öFR÷"—77VUö–Bâ"¢'VæFÆRÒfWF6…öÖ—'&÷%ö'VæFÆR‡7Fö6µö6öFRÂ—77VUö–CÖ—77VUö–BÂF–ÖV÷WC×F–ÖV÷WBÂ†VFÆW73Ö†VFÆW72ÂÖ—'&÷%÷7FGW3Ò&f÷&6VB"¢Æöö·WÒ'VæFÆRæÆöö·W ¢–bÆöö·Wç7FGW2Ò'7V66W72"÷"æ÷BÆöö·Wæ—77VUö–C ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓS"ÂFWF–ÃÖÆöö·WæÖW76vR÷"$6÷VÆBæ÷B&W6öÇfRvV&"×6—FR—77VR”Bâ"¢&W7VÇG2Ò'VæFÆRç&W7VÇG0¢'6VBÒ'6U÷&W7VÇG2€¢Æöö·Wæ—77VUö–BÀ¢&W7VÇG2À¢7Fö6µö6öFSÖÆöö·Wç7Fö6µö6öFR÷"7Fö6µö6öFRÀ¢–EöÆöö·WöÖWF†öCÖÆöö·WæÖWF†öBÀ¢–EöÆöö·W÷7FGW3ÖÆöö·Wç7FGW2À¢6÷W&6UöÖWFFFÖ'VæFÆRæÖWFFFÀ¢¢f÷"v&æ–ær–â'VæFÆRçv&æ–æw3 ¢–bv&æ–æræ÷B–â'6VBææÇ—6—5÷v&æ–æw3 ¢'6VBææÇ—6—5÷v&æ–æw2æVæB‡v&æ–ær¢&WGW&â§6öå÷6fR‡'6VE÷Fõö§6öå÷&VG’‡'6VBÂ&W7VÇG2’  ¤ævWB‚"ò"Â&W7öç6UöÖöFVÃÕ&ö÷E&W7öç6R¦FVb&ö÷B‚’ÓâF–7E·7G"Âç•Ó ¢&WGW&â°¢&ö²#¢G'VRÀ¢'6W'f–6R#¢•õD•DÄRÀ¢'fW'6–öâ#¢•õdU%4”ôâÀ¢&Æ–æ·2#¢²&†VÇF‚#¢"ö†VÇF‚"Â&÷Væ’#¢"ö÷Væ’æ§6öâ"Â'7Fö6²#¢"ö’÷7Fö6²'ÒÀ¢Ð  ¤ævWB‚"ö†VÇF‚"Â&W7öç6UöÖöFVÃÔ†VÇF…&W7öç6RÂ&W7öç6UöÖöFVÅöW†6ÇVFUöæöæSÕG'VR¦FVb†VÇF‚‡W7G&V×3¢&ööÂÒVW'’„fÇ6RÂFW67&—F–öãÒ%&ö&RvV&"×6—FRÂ„´U‚æBcW7G&V×2â"’’ÓâF–7E·7G"Âç•Ó ¢–ÆöC¢F–7E·7G"Âç•ÒÒ°¢&ö²#¢G'VRÀ¢'6W'f–6R#¢•õ4U%d”4RÀ¢'fW'6–öâ#¢•õdU%4”ôâÀ¢'WF–ÖU÷6V6öæG2#¢–çB†Ö‚ƒãÂF–ÖRæÖöæ÷Föæ–2‚’Òôõ5D%DTEôÔôäõDôä”2’’À¢Ð¢–bW7G&V×3 ¢–ÆöE²'W7G&V×2%ÒÒ&ö&U÷W7G&V×2‡F–ÖV÷WCÓR¢&WGW&â–Æö@  ¤æöåöWfVçB‚'7F'GW"¦7–æ2FVb7F'EöÖ7÷6W76–öåöÖævW"‚’ÓâæöæS ¢vÆö&ÂöÖ7÷6W76–öåö6öçFW‡@¢ÆövvW"æ–æfò‚$’WF‚6öæf–s¢•õDô´TãÒW2"ÂÖ6µ÷6V7&WB†÷2ævWFVçb‚$•õDô´Tâ"Â""’’¢–b&W7F÷&U÷6æ6†÷EöF%ög&öÕö&6·W‚“ ¢ÆövvW"æ–æfò‚%&W7F÷&VB6æ6†÷BD"g&öÒ&6·W‚W2’"ÂF%÷&W7F÷&U÷7FGW2‚’ævWB‚&F%÷&W7F÷&U÷6÷W&6R"Â'Væ¶æ÷vâ6÷W&6R"’¢öÖ7÷6W76–öåö6öçFW‡BÒÖ7÷6W'fW"ç6W76–öåöÖævW"ç'Vâ‚¢v—BöÖ7÷6W76–öåö6öçFW‡BåõöVçFW%õò‚  ¤æöåöWfVçB‚'6‡WFF÷vâ"¦7–æ2FVb7F÷öÖ7÷6W76–öåöÖævW"‚’ÓâæöæS ¢vÆö&ÂöÖ7÷6W76–öåö6öçFW‡@¢–böÖ7÷6W76–öåö6öçFW‡B—2æ÷BæöæS ¢v—BöÖ7÷6W76–öåö6öçFW‡BåõöW†—Eõò„æöæRÂæöæRÂæöæR¢öÖ7÷6W76–öåö6öçFW‡BÒæöæP  ¤ævWB‚"÷&ö&÷G2çG‡B"Â–æ6ÇVFUö–å÷66†VÖÔfÇ6R¦FVb&ö&÷G5÷G‡B‚’Óâ&W7öç6S ¢&WGW&â&W7öç6R€¢%W6W"ÖvVçC¢¥Æâ ¢$ÆÆ÷s¢ö’õÆâ ¢$ÆÆ÷s¢öÖ7Æâ ¢$ÆÆ÷s¢ö†VÇF…Æâ ¢$ÆÆ÷s¢ö÷Væ’æ§6öåÆâ ¢$F—6ÆÆ÷s¥Æâ"À¢ÖVF–÷G—SÒ'FW‡B÷Æ–â"À¢  ¤ævWB€¢"öææ÷Væ6VÖVçB÷Fb"À¢÷W&F–öåö–CÒ&fWF6„ææ÷Væ6VÖVçEFb"À¢FWVæFVæ6–W3Õ´FWVæG2‡fW&–g•ö•÷Fö¶Vâ•ÒÀ¢¦FVbfWF6…öææ÷Væ6VÖVçE÷FeöVæGö–çB€¢W&Ã¢7G"ÒVW'’‚âââÂFW67&—F–öãÒ$…EE2„´U‚ææ÷Væ6VÖVçBDbU$Ââ"’À¢Ö…ö6†'3¢–çBÒVW'’ƒSóÂFW67&—F–öãÒ$Ö†–×VÒW‡G&7FVB6†&7FW'2&WGW&æVBâ"’À¢’ÓâF–7E·7G"Âç•Ó ¢&WGW&âfWF6…ö†¶W…öææ÷Væ6VÖVçE÷Fb‡W&ÂÂÖ…ö6†'3ÖÖ…ö6†'2ÂF–ÖV÷WCÓ#ã  ¤ævWB€¢"ö’÷7Fö6²"À¢&W7öç6UöÖöFVÃÕ7Fö6´6ö×7E&W7öç6RÀ¢÷W&F–öåö–CÒ&vWD44557Fö6´FF"À¢FWVæFVæ6–W3Õ´FWVæG2‡fW&–g•ö•÷Fö¶Vâ•ÒÀ¢¦FVbvWE÷7Fö6²€¢6öFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$„²7Fö6²6öFRÂRærâS“"â"’À¢7Fö6µö6öFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$&6·v&BÖ6ö×F–&ÆRÆ–2f÷"6öFRâ"’À¢F–ÖV÷WC¢–çBÒVW'’ƒ3ÂvSÓÂÆSÓ3RÂFW67&—F–öãÒ$÷fW&ÆÂ6ö×7B’F–ÖV÷WB'VFvWB–â6V6öæG2â"’À¢†öÆF–æw5öÆ–Ö—C¢–çBÒVW'’ƒRÂvSÓÂÆSÓÂFW67&—F–öãÒ$Ö†–×VÒ†öÆF–æw2&÷w2&WGW&æVBâ"’À¢6†ævW5öÆ–Ö—C¢–çBÒVW'’ƒ#ÂvSÓÂÆSÓÂFW67&—F–öãÒ$Ö†–×VÒ6†ævW2&÷w2&WGW&æVBâ"’À¢&–uö6†ævW5öÆ–Ö—C¢–çBÒVW'’ƒÂvSÓÂÆSÓÂFW67&—F–öãÒ$Ö†–×VÒ&–r6†ævW2&÷w2&WGW&æVBâ"’À¢6öæ6VçG&F–öåöÆ–Ö—C¢–çBÒVW'’ƒRÂvSÓÂÆSÓÂFW67&—F–öãÒ$Ö†–×VÒ6öæ6VçG&F–öâ&÷w2&WGW&æVBâ"’À¢6†ævW5ög&öÓ¢7G"ÒVW'’‚""ÂGFW&ã×"%âGÅåÆG³GÒÕÆG³'ÒÕÆG³'ÒB"ÂFW67&—F–öãÒ$6†ævW2&ævR7F'C²G&FRFFR'’FVfVÇBâ"’À¢6†ævW5÷Fó¢7G"ÒVW'’‚""ÂGFW&ã×"%âGÅåÆG³GÒÕÆG³'ÒÕÆG³'ÒB"ÂFW67&—F–öãÒ$6†ævW2&ævRVæC²G&FRFFR'’FVfVÇBâ"’À¢&–uö6†ævW5ög&öÓ¢7G"ÒVW'’‚""ÂGFW&ã×"%âGÅåÆG³GÒÕÆG³'ÒÕÆG³'ÒB"ÂFW67&—F–öãÒ$&–r6†ævW2&ævR7F'C²G&FRFFR'’FVfVÇBâ"’À¢&–uö6†ævW5÷Fó¢7G"ÒVW'’‚""ÂGFW&ã×"%âGÅåÆG³GÒÕÆG³'ÒÕÆG³'ÒB"ÂFW67&—F–öãÒ$&–r6†ævW2&ævRVæC²G&FRFFR'’FVfVÇBâ"’À¢FFUö–çWEö&6—3¢7G"ÒVW'’‚'G&FR"ÂGFW&ã×"%â‡G&FWÇ6WGFÆVÖVçB’B"ÂFW67&—F–öãÒ$FFR&ævR–çWB&6—3¢G&FR†FVfVÇB’÷"6WGFÆVÖVçBâ"’À¢f÷&ÖC¢7G"ÒVW'’‚&§6öâ"ÂFW67&—F–öãÒ%&W7öç6Rf÷&ÖC¢v§6öâr†FVfVÇB’÷"vÖ&¶F÷vârâ"’À¢“ ¢&WVW7FVEö6öFRÒ6öFR÷"7Fö6µö6öFR÷"" ¢–bæ÷B&WVW7FVEö6öFS ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCÂFWF–ÃÒ%&÷f–FR6öFRâ"¢–ÆöBÒ'V–ÆE÷7Fö6µ÷–ÆöB€¢7Fö6µö6öFS×&WVW7FVEö6öFRÀ¢F–ÖV÷WC×F–ÖV÷WBÀ¢†öÆF–æw5öÆ–Ö—CÖ†öÆF–æw5öÆ–Ö—BÀ¢6†ævW5öÆ–Ö—CÖ6†ævW5öÆ–Ö—BÀ¢&–uö6†ævW5öÆ–Ö—CÖ&–uö6†ævW5öÆ–Ö—BÀ¢6öæ6VçG&F–öåöÆ–Ö—CÖ6öæ6VçG&F–öåöÆ–Ö—BÀ¢†VFÆW73ÕG'VRÀ¢6†ævW5ög&öÓÖ6†ævW5ög&öÒÀ¢6†ævW5÷FóÖ6†ævW5÷FòÀ¢&–uö6†ævW5ög&öÓÖ&–uö6†ævW5ög&öÒÀ¢&–uö6†ævW5÷FóÖ&–uö6†ævW5÷FòÀ¢FFUö–çWEö&6—3ÖFFUö–çWEö&6—2À¢¢–bf÷&ÖBæÆ÷vW"‚’–â²&Ö&¶F÷vâ"Â&ÖB'Ó ¢&WGW&âÆ–åFW‡E&W7öç6R†6ö×7E÷–ÆöE÷FõöÖ&¶F÷vâ‡–ÆöB’ÂÖVF–÷G—SÒ'FW‡BöÖ&¶F÷vã²6†'6WC×WFbÓ‚"¢&WGW&â–Æö@  ¤ævWB‚"ö’÷7Fö6²öWfVçG2"Â÷W&F–öåö–CÒ&vWE7Fö6´WfVçG2"ÂFWVæFVæ6–W3Õ´FWVæG2‡fW&–g•ö•÷Fö¶Vâ•Ò¦FVbvWE÷7Fö6µöWfVçG5öVæGö–çB€¢6öFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$„²7Fö6²6öFRÂRærâ33#â"’À¢7Fö6µö6öFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$&6·v&BÖ6ö×F–&ÆRÆ–2f÷"6öFRâ"’À¢Æ–Ö—C¢–çBÒVW'’ƒ3ÂvSÓÂÆSÓ#ÂFW67&—F–öãÒ$Ö†–×VÒWfVçB&÷w2&WGW&æVBâ"’À¢F–ÖV÷WC¢–çBÒVW'’ƒ3ÂvSÓÂÆSÓ3RÂFW67&—F–öãÒ$fWF6‚F–ÖV÷WB'VFvWB–â6V6öæG2â"’À¢’ÓâF–7E·7G"Âç•Ó ¢&WVW7FVEö6öFRÒ6öFR÷"7Fö6µö6öFR÷"" ¢–bæ÷B&WVW7FVEö6öFS ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCÂFWF–ÃÒ%&÷f–FR6öFRâ"¢&WGW&â'V–ÆEöWfVçG5÷–ÆöB‡7Fö6µö6öFS×&WVW7FVEö6öFRÂÆ–Ö—CÖÆ–Ö—BÂF–ÖV÷WC×F–ÖV÷WBÂ†VFÆW73ÕG'VR  ¤ævWB‚"ö’÷7Fö6²ööff–6W'2"Â÷W&F–öåö–CÒ&vWE7Fö6´öff–6W'2"ÂFWVæFVæ6–W3Õ´FWVæG2‡fW&–g•ö•÷Fö¶Vâ•Ò¦FVbvWE÷7Fö6µööff–6W'5öVæGö–çB€¢6öFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$„²7Fö6²6öFRÂRærâ33#â"’À¢7Fö6µö6öFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$&6·v&BÖ6ö×F–&ÆRÆ–2f÷"6öFRâ"’À¢6æ6†÷EöFFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$÷F–öæÂ•••’ÔÔÒÔDB6æ6†÷BFFRâ"’À¢F–ÖV÷WC¢–çBÒVW'’ƒ3ÂvSÓÂÆSÓ3RÂFW67&—F–öãÒ$fWF6‚F–ÖV÷WB'VFvWB–â6V6öæG2â"’À¢’ÓâF–7E·7G"Âç•Ó ¢&WVW7FVEö6öFRÒ6öFR÷"7Fö6µö6öFR÷"" ¢–bæ÷B&WVW7FVEö6öFS ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCÂFWF–ÃÒ%&÷f–FR6öFRâ"¢&WGW&â'V–ÆEööff–6W'5÷–ÆöB€¢7Fö6µö6öFS×&WVW7FVEö6öFRÂ6æ6†÷EöFFS×6æ6†÷EöFFRÂF–ÖV÷WC×F–ÖV÷WBÂ†VFÆW73ÕG'VP¢  ¤ævWB‚"ö’÷67&VVâ"Â÷W&F–öåö–CÒ'67&VVå7Fö6·2"ÂFWVæFVæ6–W3Õ´FWVæG2‡fW&–g•ö•÷Fö¶Vâ•Ò¦FVb67&VVå÷7Fö6·5öVæGö–çB€¢6öFW3¢7G"ÒVW'’‚âââÂFW67&—F–öãÒ$6öÖÖ×6W&FVB„²7Fö6²6öFW2ÂRærâS“"Ã##‚Ãcc"†Ö‚#’â"’À¢F–ÖV÷WC¢–çBÒVW'’ƒ#RÂvSÓÂÆSÓ3RÂFW67&—F–öãÒ%W"×7Fö6²fWF6‚F–ÖV÷WB'VFvWB–â6V6öæG2â"’À¢’ÓâF–7E·7G"Âç•Ó ¢6öFUöÆ—7BÒ·'Bf÷"'B–â&Rç7Æ—B‡"%²ÅÇ5Ò²"Â6öFW2’–b'Bç7G&—‚•Ð¢–bæ÷B6öFUöÆ—7C ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCÂFWF–ÃÒ%&÷f–FR6öFW2†6öÖÖ×6W&FVB„²7Fö6²6öFW2’â"¢&WGW&â'V–ÆE÷67&VVå÷–ÆöB†6öFW3Ö6öFUöÆ—7BÂF–ÖV÷WC×F–ÖV÷WB  ¤ævWB‚"ö’÷'F–6—çB"Â÷W&F–öåö–CÒ'6V&6…'F–6—çD†öÆF–æw2"ÂFWVæFVæ6–W3Õ´FWVæG2‡fW&–g•ö•÷Fö¶Vâ•Ò¦FVb6V&6…÷'F–6—çEöVæGö–çB€¢–C¢7G"ÒVW'’‚âââÂFW67&—F–öãÒ$4452'F–6—çB–BÂRærâ#cc÷"3#‚â"’À¢6öFW3¢7G"ÒVW'’‚âââÂFW67&—F–öãÒ$6öÖÖ×6W&FVB„²7Fö6²6öFW2Fò6V&6‚†Ö‚#’â"’À¢F–ÖV÷WC¢–çBÒVW'’ƒ#RÂvSÓÂÆSÓ3RÂFW67&—F–öãÒ%W"×7Fö6²fWF6‚F–ÖV÷WB'VFvWB–â6V6öæG2â"’À¢’ÓâF–7E·7G"Âç•Ó ¢6öFUöÆ—7BÒ·'Bf÷"'B–â&Rç7Æ—B‡"%²ÅÇ5Ò²"Â6öFW2’–b'Bç7G&—‚•Ð¢&WGW&â'V–ÆE÷'F–6—çE÷6V&6…÷–ÆöB‡'F–6—çEö–CÖ–BÂ6öFW3Ö6öFUöÆ—7BÂF–ÖV÷WC×F–ÖV÷WB  ¤ævWB‚"ö’÷7Fö6²÷&–6R"Â÷W&F–öåö–CÒ&vWE7Fö6µ&–6T†—7F÷'’"ÂFWVæFVæ6–W3Õ´FWVæG2‡fW&–g•ö•÷Fö¶Vâ•Ò¦FVbvWE÷7Fö6µ÷&–6UöVæGö–çB€¢6öFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$„²7Fö6²6öFRÂRærâ##‚â"’À¢7Fö6µö6öFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$&6·v&BÖ6ö×F–&ÆRÆ–2f÷"6öFRâ"’À¢Æ–Ö—C¢–çBÒVW'’ƒƒÂvSÓÂÆSÓ#ÂFW67&—F–öãÒ$Ö†–×VÒ&–6RÖ†—7F÷'’&÷w2&WGW&æVBâ"’À¢F—3¢–çBÒVW'’ƒ“RÂvSÓÂÆSÓ3cSÂFW67&—F–öãÒ%–†öòÆöö¶&6²v–æF÷r–âF—2â"’À¢7F'EöFFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$÷F–öæÂ•••’ÔÔÒÔDB7F'BFFRf÷"F†R–†öò&ævRâ"’À¢F–ÖV÷WC¢–çBÒVW'’ƒ3ÂvSÓÂÆSÓ3RÂFW67&—F–öãÒ$fWF6‚F–ÖV÷WB'VFvWB–â6V6öæG2â"’À¢’ÓâF–7E·7G"Âç•Ó ¢&WVW7FVEö6öFRÒ6öFR÷"7Fö6µö6öFR÷"" ¢–bæ÷B&WVW7FVEö6öFS ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCÂFWF–ÃÒ%&÷f–FR6öFRâ"¢&WGW&â'V–ÆE÷&–6Uö†—7F÷'•÷–ÆöB€¢7Fö6µö6öFS×&WVW7FVEö6öFRÀ¢Æ–Ö—CÖÆ–Ö—BÀ¢F—3ÖF—2À¢7F'EöFFS×7F'EöFFRÀ¢F–ÖV÷WC×F–ÖV÷WBÀ¢†VFÆW73ÕG'VRÀ¢  ¤ævWB‚"ö’÷7Fö6²öææ÷Væ6VÖVçG2"Â÷W&F–öåö–CÒ&vWE7Fö6´ææ÷Væ6VÖVçG2"ÂFWVæFVæ6–W3Õ´FWVæG2‡fW&–g•ö•÷Fö¶Vâ•Ò¦FVbvWE÷7Fö6µöææ÷Væ6VÖVçG5öVæGö–çB€¢6öFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$„²7Fö6²6öFRÂRærâ##‚â"’À¢7Fö6µö6öFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$&6·v&BÖ6ö×F–&ÆRÆ–2f÷"6öFRâ"’À¢W&–öE÷–V'3¢–çBÒVW'’ƒÂvSÓÂÆSÓ"ÂFW67&—F–öãÒ$ææ÷Væ6VÖVçBÆöö¶&6²W&–öB–â–V'2â"’À¢Æ–Ö—C¢–çBÒVW'’ƒÂvSÓÂÆSÓ#ÂFW67&—F–öãÒ$Ö†–×VÒææ÷Væ6VÖVçB&÷w2&WGW&æVBâ"’À¢F–ÖV÷WC¢–çBÒVW'’ƒ3ÂvSÓÂÆSÓ3RÂFW67&—F–öãÒ$fWF6‚F–ÖV÷WB'VFvWB–â6V6öæG2â"’À¢’ÓâF–7E·7G"Âç•Ó ¢&WVW7FVEö6öFRÒ6öFR÷"7Fö6µö6öFR÷"" ¢–bæ÷B&WVW7FVEö6öFS ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCÂFWF–ÃÒ%&÷f–FR6öFRâ"¢&WGW&â'V–ÆEö†¶W…öææ÷Væ6VÖVçG5÷–ÆöB‡7Fö6µö6öFS×&WVW7FVEö6öFRÂW&–öE÷–V'3×W&–öE÷–V'2ÂÆ–Ö—CÖÆ–Ö—BÂF–ÖV÷WC×F–ÖV÷WB  ¤ævWB‚"ö’÷7Fö6²öF–fb"Â÷W&F–öåö–CÒ&vWD4454F–fb"ÂFWVæFVæ6–W3Õ´FWVæG2‡fW&–g•ö•÷Fö¶Vâ•Ò¦FVbvWEö6675öF–feöVæGö–çB€¢6öFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$„²7Fö6²6öFRÂRærâ##‚â"’À¢7Fö6µö6öFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$&6·v&BÖ6ö×F–&ÆRÆ–2f÷"6öFRâ"’À¢FFUö¢7G"ÒVW'’‚âââÂFW67&—F–öãÒ$V&Æ–W"FFRÂ•••’ÔÔÒÔDBâ"’À¢FFUö#¢7G"ÒVW'’‚âââÂFW67&—F–öãÒ$ÆFW"FFRÂ•••’ÔÔÒÔDBâ"’À¢F–ÖV÷WC¢–çBÒVW'’ƒ3ÂvSÓÂÆSÓ3RÂFW67&—F–öãÒ$fWF6‚F–ÖV÷WB'VFvWB–â6V6öæG2â"’À¢’ÓâF–7E·7G"Âç•Ó ¢&WVW7FVEö6öFRÒ6öFR÷"7Fö6µö6öFR÷"" ¢–bæ÷B&WVW7FVEö6öFS ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCÂFWF–ÃÒ%&÷f–FR6öFRâ"¢&WGW&â'V–ÆEöF–fe÷–ÆöB‡7Fö6µö6öFS×&WVW7FVEö6öFRÂFFUöÖFFUöÂFFUö#ÖFFUö"ÂF–ÖV÷WC×F–ÖV÷WB  ¤ævWB‚"ö’÷7Fö6²ö6—FÂ"Â÷W&F–öåö–CÒ&vWE7Fö6´6—FÂ"ÂFWVæFVæ6–W3Õ´FWVæG2‡fW&–g•ö•÷Fö¶Vâ•Ò¦FVbvWE÷7Fö6µö6—FÅöVæGö–çB€¢6öFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$„²7Fö6²6öFRÂRærâ##‚â"’À¢7Fö6µö6öFS¢7G"ÂæöæRÒVW'’„æöæRÂFW67&—F–öãÒ$&6·v&BÖ6ö×F–&ÆRÆ–2f÷"6öFRâ"’À¢6†ævW5öÆ–Ö—C¢–çBÒVW'’ƒ3ÂvSÓÂÆSÓ#ÂFW67&—F–öãÒ$Ö†–×VÒ6†&RÖ6†ævR&÷w2&WGW&æVBâ"’À¢'W–&6·5öÆ–Ö—C¢–çBÒVW'’ƒ#ÂvSÓÂÆSÓ#ÂFW67&—F–öãÒ$Ö†–×VÒ'W–&6²&÷w2&WGW&æVBâ"’À¢F–ÖV÷WC¢–çBÒVW'’ƒ3ÂvSÓÂÆSÓ3RÂFW67&—F–öãÒ$fWF6‚F–ÖV÷WB'VFvWB–â6V6öæG2â"’À¢’ÓâF–7E·7G"Âç•Ó ¢&WVW7FVEö6öFRÒ6öFR÷"7Fö6µö6öFR÷"" ¢–bæ÷B&WVW7FVEö6öFS ¢&—6R…EEW†6WF–öâ‡7FGW5ö6öFSÓCÂFWF–ÃÒ%&÷f–FR6öFRâ"¢&WGW&â'V–ÆEö6—FÅ÷–ÆöB€¢7Fö6µö6öFS×&WVW7FVEö6öFRÂ6†ævW5öÆ–Ö—CÖ6†ævW5öÆ–Ö—BÂ'W–&6·5öÆ–Ö—CÖ'W–&6·5öÆ–Ö—BÂF–ÖV÷WC×F–ÖV÷W@¢  ¤ævWB‚"ö’÷6æ6†÷G2öW‡÷'B"ÂFWVæFVæ6–W3Õ´FWVæG2‡fW&–g•ö•÷Fö¶Vâ•Ò¦FVbW‡÷'E÷6æ6†÷G2‚’Óâ&W7öç6S ¢&WGW&â&W7öç6R€¢W‡÷'EöF%ö'—FW2‚’À¢ÖVF–÷G—SÒ&Æ–6F–öâöö7FWB×7G&VÒ"À¢†VFW'3×²$6öçFVçBÔF—7÷6—F–öâ#¢vGF6†ÖVçC²f–ÆVæÖSÒ&6675÷6æ6†÷G2æF""wÒÀ¢  ¤ævWB‚"ö’÷6æ6†÷EöÆÂ"ÂFWVæFVæ6–W3Õ´FWVæG2‡fW&–g•ö•÷Fö¶Vâ•Ò¦FVb6æ6†÷EöÆÂ€¢F–ÖV÷WC¢–çBÒVW'’ƒ3ÂvSÓÂÆSÓc’À¢w&÷W¢7G"ÂæöæRÒVW'’„æöæRÂGFW&ãÒ%â†6–¦—ÆÇ6†R’B"ÂFW67&—F–öãÒ$÷F–öæÂvF6†Æ—7Bw&÷Wf–ÇFW"â"’À¢’ÓâF–7E·7G"Âç•Ó ¢&÷w2ÒµÐ¢VçG&–W2ÒÆöE÷vF6†Æ—7EöVçG&–W2†w&÷WÖw&÷W¢f÷"VçG'’–âVçG&–W3 ¢6öFRÒVçG'’æ6öFP¢&÷w2æVæB€¢°¢&6öFR#¢6öFRÀ¢&æÖR#¢VçG'’ææÖRÀ¢&w&÷W#¢#²"æ¦ö–â†VçG'’æw&÷W2’À¢&ö²#¢G'VRÀ¢'6¶—VB#¢G'VRÀ¢&ÖW76vR#¢$Æ—fR„´U‚4Er6æ6†÷BfWF6‚—2F—6&ÆVC²W†—7F–ærÆö6ÂD"v2æ÷BÖöF–f–VBâ"À¢Ð¢ ¢&–6U÷&W7VÇBÒfWF6…÷–†öõ÷&–6Uö†—7F÷'’†6öFRÂW&–öEöF—3ÓrÂ6ÆVW÷6V6öæG3Óã¢–b&–6U÷&W7VÇBæö³ ¢&–6U÷&÷w2ÒW6W'E÷&–6Uö†—7F÷'’†6öFRÂ&–6U÷&W7VÇBçF&ÆRÂ6÷W&6SÒ'–†öò"¢&÷w5²ÓÕ²'&–6Uöö²%ÒÒG'VP¢&÷w5²ÓÕ²'&–6U÷&÷w2%ÒÒ&–6U÷&÷w0¢&÷w5²ÓÕ²'&–6U÷6÷W&6R%ÒÒ'–†öò ¢VÇ6S ¢&÷w5²ÓÕ²'&–6Uöö²%ÒÒfÇ6P¢&÷w5²ÓÕ²'&–6UöW'&÷%÷G—R%ÒÒ&–6U÷&W7VÇBæW'&÷%÷G—P¢&÷w5²ÓÕ²'&–6UöW'&÷%öÖW76vR%ÒÒ&–6U÷&W7VÇBæW'&÷%öÖW76vP¢öµö6÷VçBÒ7VÒƒf÷"&÷r–â&÷w2–b&÷rævWB‚&ö²"’æB&÷rævWB‚'&–6Uöö²"ÂG'VR’—2æ÷BfÇ6R¢f–ÆVEö6÷VçBÒ7VÒƒf÷"&÷r–â&÷w2–bæ÷B&÷rævWB‚&ö²"’÷"&÷rævWB‚'&–6Uöö²"ÂG'VR’—2fÇ6R¢&WGW&â²&ö²#¢f–ÆVEö6÷VçBÓÒÂ&w&÷W#¢w&÷W÷"&ÆÂ"Â'F÷FÂ#¢ÆVâ‡&÷w2’Â&öµö6÷VçB#¢öµö6÷VçBÂ&f–ÆVEö6÷VçB#¢f–ÆVEö6÷VçBÂ&F%÷F‚#¢7G"„D%õD‚’Â'&W7VÇG2#¢&÷w7Ð  ¤ævWB‚"ö’÷7Fö6²ögVÆÂ"Â–æ6ÇVFUö–å÷66†VÖÔfÇ6RÂFWVæFVæ6–W3Õ´FWVæG2‡fW&–g•ö&V&W%÷Fö¶Vâ•Ò¦FVbvWE÷7Fö6µögVÆÂ€¢7Fö6µö6öFS¢7G"ÒVW'’‚""ÂFW67&—F–öãÒ$„²7Fö6²6öFRÂRærâS“"â"’À¢—77VUö–C¢7G"ÒVW'’‚""ÂFW67&—F–öãÒ$÷F–öæÂvV&"×6—FR—77VR”Bâ"’À¢F–ÖV÷WC¢–çBÒVW'’ƒcÂvSÓÂÆSÓ#ÂFW67&—F–öãÒ%F–ÖV÷WBW"6÷W&6RvR–â6V6öæG2â"’À¢’ÓâF–7E·7G"Âç•Ó ¢&WGW&â'V–ÆEögVÆÅ÷7Fö6µ÷–ÆöB‡7Fö6µö6öFS×7Fö6µö6öFRÂ—77VUö–CÖ—77VUö–BÂF–ÖV÷WC×F–ÖV÷WBÂ†VFÆW73ÕG'VR  ¦æÖ÷VçB‚"öÖ7"ÂÖ7÷6W'fW"ç7G&VÖ&ÆUö‡GGö‚’