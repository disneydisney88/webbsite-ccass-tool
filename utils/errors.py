"""Structured error codes for REST + MCP responses (handover 1.2).

Failures surface as {"error_code", "message", "retry_recommended"} objects so a
client's retry logic can act on them: retry a COLD_START / SOURCE_TIMEOUT, but
do not waste retries on a PARSE_ERROR / INVALID_CODE.
"""

from __future__ import annotations

from typing import Any

# error_code -> whether a retry is worth attempting
RETRYABLE = {
    "COLD_START": True,
    "SOURCE_TIMEOUT": True,
    "SOURCE_FETCH_FAILED": True,
    "MIRROR_BLOCKED": False,
    "SOURCE_CHALLENGE": False,
    "SOURCE_CHANGED": False,
    "PARSE_ERROR": False,
    "TOO_LARGE": False,
    "LOCAL_SNAPSHOT_EMPTY": False,
    "INVALID_DATE_RANGE": False,
    "INVALID_CODE": False,
    "AUTH_FAILED": False,
    "ISSUE_LOOKUP_FAILED": True,
}


def structured_error(error_code: str, message: str) -> dict[str, Any]:
    return {
        "error_code": error_code,
        "message": message,
        "retry_recommended": RETRYABLE.get(error_code, False),
    }


def classify_fetch_message(error_type: str | None, message: str | None) -> str:
    """Map a FetchResult's error_type/message to an error_code."""
    text = f"{error_type or ''} {message or ''}".lower()
    if "timeoutbudget" in text or "budget exhausted" in text:
        return "COLD_START"
    if "timeout" in text or "timed out" in text:
        return "SOURCE_TIMEOUT"
    if "source_challenge" in text or "cookie/reload challenge" in text or "human verification" in text:
        return "SOURCE_CHALLENGE"
    if "no table" in text or "no matching table" in text or "table format" in text:
        return "SOURCE_CHANGED"
    if "parsing failed" in text or "parse" in text:
        return "PARSE_ERROR"
    if any(token in text for token in ("connection", "dns", "name resolution", "refused", "reset", "402", "403", "429", "502", "503", "504")):
        return "SOURCE_FETCH_FAILED"
    if "403" in text or "forbidden" in text:
        return "SOURCE_FETCH_FAILED"
    return "PARSE_ERROR"


def errors_from_fetch_summary(fetch_summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build structured errors from failed rows in a fetch_summary."""
    errors: list[dict[str, Any]] = []
    for row in fetch_summary or []:
        section = row.get("Section") or row.get("section") or "Unknown section"
        status_text = str(row.get("Status") or row.get("ok") or "").lower()
        error_message = row.get("Error") or row.get("error_message") or ""
        error_type = row.get("Error type") or row.get("error_type") or ""
        failed = status_text in {"failed", "false"} or bool(error_message)
        if not failed:
            continue
        section_lower = str(section).strip().lower()
        error_lower = str(error_message).lower()
        if section_lower == "big changes" and any(
            phrase in error_lower
            for phrase in (
                "local history",
                "not enough sdw snapshots",
                "mirror historical data",
                "big changes require",
            )
        ):
            continue
        code = classify_fetch_message(error_type, error_message)
        errors.append(structured_error(code, f"{section}: {error_message or status_text}"))
    return errors


def errors_from_warnings(warnings: list[str]) -> list[dict[str, Any]]:
    """Classify free-text analysis warnings into structured errors."""
    errors: list[dict[str, Any]] = []
    for warning in warnings or []:
        lower = warning.lower()
        if "mirror_blocked" in lower or "blocked_by_cloudflare" in lower:
            code = "MIRROR_BLOCKED"
        elif "local_snapshot_empty" in lower:
            code = "LOCAL_SNAPSHOT_EMPTY"
        elif "issue lookup failed" in lower or "issue id" in lower:
            code = "ISSUE_LOOKUP_FAILED"
        elif "budget exhausted" in lower or "cold" in lower:
            code = "COLD_START"
        elif "timeout" in lower:
            code = "SOURCE_TIMEOUT"
        elif "may be stale" in lower or "abnormal" in lower:
            continue  # data-quality note, not a fetch error
        else:
            continue
        errors.append(structured_error(code, warning))
    return errors
