from __future__ import annotations

import os
import re
import secrets
from typing import Any

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from utils.exporters import parsed_to_json_ready
from utils.fetcher import IssueLookup, clean_stock_code, fetch_all, resolve_issue_id_from_stock
from utils.parser import parse_results


API_TITLE = "Webb-site CCASS Research API"
API_VERSION = "1.0.0"

bearer_scheme = HTTPBearer(auto_error=False)

app = FastAPI(
    title=API_TITLE,
    version=API_VERSION,
    description="Read-only API for Webb-site CCASS research-ready summaries.",
)


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


def build_stock_payload(stock_code: str, issue_id: str = "", timeout: int = 60, headless: bool = True) -> dict[str, Any]:
    stock_code = clean_stock_code(stock_code)
    issue_id = re.sub(r"\D", "", issue_id)

    if not stock_code and not issue_id:
        raise HTTPException(status_code=400, detail="Provide stock_code or issue_id.")

    if stock_code:
        lookup = resolve_issue_id_from_stock(stock_code, timeout=timeout, headless=headless)
        if lookup.status != "success" or not lookup.issue_id:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": lookup.message or "Could not resolve Webb-site issue ID.",
                    "lookup": lookup.__dict__,
                },
            )
        issue_id = lookup.issue_id
    else:
        lookup = make_lookup_from_issue_id(issue_id)

    results = fetch_all(issue_id, stock_code=stock_code, timeout=timeout, headless=headless)
    parsed = parse_results(
        issue_id,
        results,
        stock_code=lookup.stock_code or stock_code,
        id_lookup_method=lookup.method,
        id_lookup_status=lookup.status,
    )
    exported = parsed_to_json_ready(parsed, results)
    metadata = exported.get("metadata", {})

    return json_safe(
        {
            "stock_code": metadata.get("stock_code", ""),
            "stock_name": metadata.get("stock_name", ""),
            "issue_id": metadata.get("issue_id", issue_id),
            "holdings_latest_date": metadata.get("holdings_data_date", ""),
            "changes_trading_date": metadata.get("changes_trading_date", ""),
            "total_in_ccass_percent": metadata.get("total_in_ccass_pct", ""),
            "top_5_percent": metadata.get("top5_cumulative_pct", ""),
            "top_10_percent": metadata.get("top10_cumulative_pct", ""),
            "largest_participant": metadata.get("largest_participant", ""),
            "holdings": exported.get("holdings", []),
            "changes": exported.get("changes", []),
            "big_changes": exported.get("bigchanges", []),
            "concentration": exported.get("concentration", []),
            "fetch_summary": exported.get("fetch_summary", []),
            "data_quality_warnings": exported.get("analysis_warnings", []),
        }
    )


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "ok": True,
        "service": API_TITLE,
        "version": API_VERSION,
        "links": {"health": "/health", "openapi": "/openapi.json", "stock": "/api/stock"},
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": API_TITLE, "version": API_VERSION}


@app.get("/api/stock", dependencies=[Depends(verify_bearer_token)])
def get_stock(
    stock_code: str = Query(..., description="HK stock code, e.g. 01592."),
    timeout: int = Query(60, ge=10, le=120, description="Timeout per source page in seconds."),
) -> dict[str, Any]:
    return build_stock_payload(stock_code=stock_code, timeout=timeout, headless=True)
