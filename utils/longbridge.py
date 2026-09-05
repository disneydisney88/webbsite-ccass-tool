from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from cryptography.fernet import Fernet, InvalidToken

from .fetcher import clean_stock_code, now_iso
from .snapshot_db import (
    DB_PATH,
    delete_longbridge_secret,
    load_longbridge_credential,
    load_longbridge_holdings,
    load_longbridge_secret,
    load_longbridge_snapshot_dates,
    save_longbridge_credential,
    save_longbridge_secret,
    upsert_longbridge_holdings,
)


MAIN_ENDPOINT = "https://mcp.longbridge.com"
AGENT_ENDPOINT = f"{MAIN_ENDPOINT}/agent"
OAUTH_BASE_URL = "https://openapi.longbridge.com/oauth2"
OAUTH_REGISTER_ENDPOINT = f"{OAUTH_BASE_URL}/register"
OAUTH_DEVICE_ENDPOINT = f"{OAUTH_BASE_URL}/device/authorize"
OAUTH_TOKEN_ENDPOINT = f"{OAUTH_BASE_URL}/token"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
OAUTH_REQUEST_TIMEOUT_SECONDS = 10.0
DEVICE_SESSION_PREFIX = "device:"
REGISTRATION_CREDENTIAL_ID = "oauth_registration"
READ_ONLY_TOOLS = frozenset(
    {
        "broker_holding_detail",
        "broker_holding",
        "broker_holding_daily",
        "participants",
        "static_info",
        "history_candlesticks_by_date",
    }
)
PARTICIPANT_ID_RE = re.compile(r"^[A-Ca-c]\d{5}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class LongbridgeError(RuntimeError):
    pass


class LongbridgeAuthError(LongbridgeError):
    pass


@dataclass
class LongbridgeData:
    code: str
    data_date: str = ""
    name: str = ""
    issued_shares: int | None = None
    holdings: list[dict[str, Any]] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)
    concentration: list[dict[str, Any]] = field(default_factory=list)
    big_changes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tool_results: dict[str, Any] = field(default_factory=dict)


def to_longbridge_symbol(code: str) -> str:
    normalized = clean_stock_code(code)
    if not normalized:
        raise ValueError("Hong Kong stock code must contain 1 to 5 digits.")
    return f"{int(normalized)}.HK"


def _json_rpc_result(response: requests.Response) -> dict[str, Any]:
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        messages = []
        for line in response.text.splitlines():
            if line.startswith("data:"):
                try:
                    messages.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    continue
        payload = messages[-1] if messages else {}
    else:
        payload = response.json()
    if payload.get("error"):
        error = payload["error"]
        raise LongbridgeError(str(error.get("message") or error))
    return payload.get("result") or {}


class LongbridgeMCPClient:
    def __init__(self, endpoint: str = MAIN_ENDPOINT, token: str = "", timeout: float = 20.0):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "User-Agent": "webbsite-ccass-tool/longbridge-readonly",
            }
        )
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self._request_id = 0
        self._initialized = False

    def _post(self, method: str, params: dict[str, Any] | None = None, notification: bool = False) -> dict[str, Any]:
        self._request_id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notification:
            body["id"] = self._request_id
        try:
            response = self.session.post(self.endpoint, json=body, timeout=self.timeout)
        except requests.RequestException as exc:
            raise LongbridgeError(f"Longbridge MCP request failed: {type(exc).__name__}: {exc}") from exc
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self.session.headers["Mcp-Session-Id"] = session_id
        if response.status_code in {401, 403}:
            raise LongbridgeAuthError(f"Longbridge authorization rejected with HTTP {response.status_code}.")
        if notification:
            response.raise_for_status()
            return {}
        try:
            return _json_rpc_result(response)
        except requests.RequestException as exc:
            raise LongbridgeError(f"Longbridge MCP returned an HTTP error: {exc}") from exc

    def initialize(self) -> None:
        if self._initialized:
            return
        self._post(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "webbsite-ccass-tool", "version": "1.0"},
            },
        )
        self._post("notifications/initialized", notification=True)
        self._initialized = True

    def list_tools(self) -> list[dict[str, Any]]:
        self.initialize()
        return list(self._post("tools/list").get("tools") or [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in READ_ONLY_TOOLS:
            raise PermissionError(f"Longbridge tool is not in the read-only whitelist: {name}")
        self.initialize()
        return self._post("tools/call", {"name": name, "arguments": arguments})


def _fernet() -> Fernet:
    secret = os.getenv("LONGBRIDGE_TOKEN_KEY", "").strip()
    if not secret:
        raise LongbridgeAuthError("LONGBRIDGE_TOKEN_KEY is required for encrypted token storage.")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def save_token_payload(payload: dict[str, Any], path: Path = DB_PATH) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    token_file = os.getenv("LONGBRIDGE_TOKEN_FILE", "").strip()
    encrypted = _fernet().encrypt(raw)
    if token_file:
        target = Path(token_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encrypted)
        try:
            target.chmod(0o600)
        except OSError:
            pass
        return
    save_longbridge_credential(encrypted, path=path)


def load_token_payload(path: Path = DB_PATH) -> dict[str, Any] | None:
    token_file = os.getenv("LONGBRIDGE_TOKEN_FILE", "").strip()
    encrypted = Path(token_file).read_bytes() if token_file and Path(token_file).exists() else load_longbridge_credential(path=path)
    if not encrypted:
        return None
    try:
        return json.loads(_fernet().decrypt(encrypted).decode("utf-8"))
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise LongbridgeAuthError("Stored Longbridge credential cannot be decrypted or is invalid.") from exc


def _save_encrypted_secret(credential_id: str, payload: dict[str, Any], path: Path) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    save_longbridge_secret(credential_id, _fernet().encrypt(raw), path=path)


def _load_encrypted_secret(credential_id: str, path: Path) -> dict[str, Any] | None:
    encrypted = load_longbridge_secret(credential_id, path=path)
    if not encrypted:
        return None
    try:
        return json.loads(_fernet().decrypt(encrypted).decode("utf-8"))
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise LongbridgeAuthError("Stored Longbridge OAuth state cannot be decrypted or is invalid.") from exc


def _post_json(
    url: str,
    *,
    timeout: float,
    json_body: dict[str, Any] | None = None,
    form: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[requests.Response, dict[str, Any]]:
    try:
        response = requests.post(url, json=json_body, data=form, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise LongbridgeAuthError(f"Longbridge OAuth request failed: {type(exc).__name__}: {exc}") from exc
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        payload = {}
    return response, payload if isinstance(payload, dict) else {}


def _oauth_registration(path: Path, timeout: float) -> dict[str, Any]:
    existing = _load_encrypted_secret(REGISTRATION_CREDENTIAL_ID, path)
    if existing and existing.get("client_id"):
        return existing
    response, payload = _post_json(
        OAUTH_REGISTER_ENDPOINT,
        timeout=timeout,
        json_body={
            "client_name": "Webb-site CCASS Tool (Longbridge OAuth)",
            "redirect_uris": ["http://localhost:60355/callback"],
            "token_endpoint_auth_method": "none",
            "grant_types": [DEVICE_GRANT_TYPE, "refresh_token"],
        },
    )
    if not response.ok or not payload.get("client_id"):
        detail = payload.get("error_description") or payload.get("error") or response.text[:300]
        raise LongbridgeAuthError(f"Longbridge OAuth client registration failed (HTTP {response.status_code}): {detail}")
    registration = {
        "client_id": str(payload["client_id"]),
        "registration_access_token": payload.get("registration_access_token"),
        "registration_client_uri": payload.get("registration_client_uri"),
        "registered_at": now_iso(),
    }
    _save_encrypted_secret(REGISTRATION_CREDENTIAL_ID, registration, path)
    return registration


def start_device_authorization(timeout: float = OAUTH_REQUEST_TIMEOUT_SECONDS, path: Path = DB_PATH) -> dict[str, Any]:
    registration = _oauth_registration(path, timeout)
    response, payload = _post_json(
        OAUTH_DEVICE_ENDPOINT,
        timeout=timeout,
        form={"client_id": registration["client_id"]},
    )
    if not response.ok or not payload.get("device_code"):
        detail = payload.get("error_description") or payload.get("error") or response.text[:300]
        raise LongbridgeAuthError(f"Longbridge Device Authorization failed (HTTP {response.status_code}): {detail}")
    expires_in = max(1, int(payload.get("expires_in") or 300))
    interval = max(1, int(payload.get("interval") or 5))
    session_id = secrets.token_urlsafe(24)
    created = datetime.now(timezone.utc)
    pending = {
        "client_id": registration["client_id"],
        "device_code": str(payload["device_code"]),
        "created_at": created.isoformat(),
        "expires_at": (created + timedelta(seconds=expires_in)).isoformat(),
        "interval": interval,
        "next_poll_at": created.isoformat(),
        "next_region": "ap",
    }
    _save_encrypted_secret(f"{DEVICE_SESSION_PREFIX}{session_id}", pending, path)
    return {
        "session_id": session_id,
        "verification_url": str(payload.get("verification_uri_complete") or payload.get("verification_uri") or ""),
        "user_code": str(payload.get("user_code") or ""),
        "expires_in": expires_in,
        "interval": interval,
        "status": "authorization_pending",
    }


def _token_expiry(payload: dict[str, Any]) -> datetime | None:
    value = payload.get("expires_at")
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _store_oauth_token(
    payload: dict[str, Any],
    client_id: str,
    path: Path,
    dc_region: str = "",
) -> dict[str, Any]:
    access_token = str(payload.get("access_token") or "")
    if not access_token:
        raise LongbridgeAuthError("Longbridge OAuth response did not include an access token.")
    expires_in = max(1, int(payload.get("expires_in") or 3600))
    stored = {
        "client_id": client_id,
        "access_token": access_token,
        "refresh_token": payload.get("refresh_token"),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat(),
        "authenticated_at": now_iso(),
        "auth_method": "oauth_device_flow",
        "dc_region": dc_region,
    }
    save_token_payload(stored, path=path)
    return stored


def poll_device_authorization(session_id: str, timeout: float = 20.0, path: Path = DB_PATH) -> dict[str, Any]:
    credential_id = f"{DEVICE_SESSION_PREFIX}{str(session_id or '').strip()}"
    pending = _load_encrypted_secret(credential_id, path)
    if not pending:
        raise LongbridgeAuthError("Longbridge device authorization session was not found or has expired.")
    now = datetime.now(timezone.utc)
    expires_at = _token_expiry(pending)
    if expires_at and now >= expires_at:
        delete_longbridge_secret(credential_id, path=path)
        raise LongbridgeAuthError("Longbridge device authorization expired; start a new login.")
    next_poll_at = _token_expiry({"expires_at": pending.get("next_poll_at")})
    if next_poll_at and now < next_poll_at:
        return {"status": "authorization_pending", "retry_after": max(1, int((next_poll_at - now).total_seconds()))}
    region = str(pending.get("next_region") or "ap")
    response, payload = _post_json(
        OAUTH_TOKEN_ENDPOINT,
        timeout=timeout,
        headers={"x-dc-region": region},
        form={
            "client_id": pending["client_id"],
            "grant_type": DEVICE_GRANT_TYPE,
            "device_code": pending["device_code"],
        },
    )
    if response.ok and payload.get("access_token"):
        stored = _store_oauth_token(payload, str(pending["client_id"]), path, dc_region=region)
        delete_longbridge_secret(credential_id, path=path)
        return {"status": "authenticated", "expires_at": stored["expires_at"], "refresh_available": bool(stored.get("refresh_token"))}
    error = str(payload.get("error") or "")
    if error in {"authorization_pending", "slow_down"}:
        interval = int(pending.get("interval") or 5) + (5 if error == "slow_down" else 0)
        pending["interval"] = interval
        pending["next_poll_at"] = (now + timedelta(seconds=interval)).isoformat()
        pending["next_region"] = "us" if region == "ap" else "ap"
        _save_encrypted_secret(credential_id, pending, path)
        return {"status": error, "retry_after": interval}
    detail = payload.get("error_description") or error or response.text[:300]
    delete_longbridge_secret(credential_id, path=path)
    raise LongbridgeAuthError(f"Longbridge device authorization failed (HTTP {response.status_code}): {detail}")


def refresh_token_payload(payload: dict[str, Any], timeout: float = 20.0, path: Path = DB_PATH) -> dict[str, Any]:
    expires_at = _token_expiry(payload)
    if not expires_at or expires_at > datetime.now(timezone.utc) + timedelta(seconds=60):
        return payload
    refresh_token = str(payload.get("refresh_token") or "")
    client_id = str(payload.get("client_id") or "")
    if not refresh_token or not client_id:
        raise LongbridgeAuthError("Longbridge credential expired and cannot be refreshed; authenticate again.")
    response, refreshed = _post_json(
        OAUTH_TOKEN_ENDPOINT,
        timeout=timeout,
        headers={"x-dc-region": str(payload.get("dc_region"))} if payload.get("dc_region") else None,
        form={"client_id": client_id, "grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    if not response.ok or not refreshed.get("access_token"):
        detail = refreshed.get("error_description") or refreshed.get("error") or response.text[:300]
        raise LongbridgeAuthError(f"Longbridge token refresh failed (HTTP {response.status_code}): {detail}")
    if not refreshed.get("refresh_token"):
        refreshed["refresh_token"] = refresh_token
    return _store_oauth_token(refreshed, client_id, path, dc_region=str(payload.get("dc_region") or ""))


def active_token_payload(timeout: float = 20.0, path: Path = DB_PATH) -> dict[str, Any] | None:
    payload = load_token_payload(path=path)
    return refresh_token_payload(payload, timeout=timeout, path=path) if payload else None


def list_longbridge_tools(timeout: float = 20.0, path: Path = DB_PATH) -> dict[str, Any]:
    """Verify the stored credential with tools/list and expose read-only schemas."""
    token_payload = active_token_payload(timeout=timeout, path=path)
    if not token_payload or not token_payload.get("access_token"):
        raise LongbridgeAuthError("Longbridge is not authenticated.")
    client = LongbridgeMCPClient(token=str(token_payload["access_token"]), timeout=timeout)
    tools = client.list_tools()
    allowed = [tool for tool in tools if str(tool.get("name") or "") in READ_ONLY_TOOLS]
    available_names = {str(tool.get("name") or "") for tool in allowed}
    return {
        "endpoint": MAIN_ENDPOINT,
        "method": "tools/list",
        "tool_count": len(tools),
        "read_only_tools": allowed,
        "missing_read_only_tools": sorted(READ_ONLY_TOOLS - available_names),
    }


def call_longbridge_read_only_tool(
    name: str,
    arguments: dict[str, Any],
    timeout: float = 20.0,
    path: Path = DB_PATH,
) -> dict[str, Any]:
    """Call one explicitly whitelisted Longbridge research tool."""
    if name not in READ_ONLY_TOOLS:
        raise PermissionError("Longbridge tool is not in the read-only whitelist.")
    token_payload = active_token_payload(timeout=timeout, path=path)
    if not token_payload or not token_payload.get("access_token"):
        raise LongbridgeAuthError("Longbridge is not authenticated.")
    client = LongbridgeMCPClient(token=str(token_payload["access_token"]), timeout=timeout)
    return client.call_tool(name, dict(arguments or {}))


def _unwrap_tool_result(result: dict[str, Any]) -> Any:
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    for item in result.get("content") or []:
        if item.get("type") != "text":
            continue
        text = str(item.get("text") or "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            if text:
                return {"message": text}
    return result


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _first(record: dict[str, Any], names: tuple[str, ...], default: Any = None) -> Any:
    lower = {str(key).lower(): value for key, value in record.items()}
    for name in names:
        if name.lower() in lower and lower[name.lower()] not in (None, ""):
            return lower[name.lower()]
    return default


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> str:
    text = str(value or "").strip()[:10].replace("/", "-")
    return text if ISO_DATE_RE.fullmatch(text) else ""


def _percentage(record: dict[str, Any]) -> float | None:
    percent = _number(_first(record, ("stake_pct_of_issued", "holding_percent", "holding_pct", "percent")))
    if percent is not None:
        return percent
    ratio = _number(_first(record, ("ratio", "holding_ratio")))
    return ratio * 100 if ratio is not None and abs(ratio) <= 1 else ratio


def _record_list(payload: Any) -> list[dict[str, Any]]:
    candidates = []
    for item in _walk(payload):
        participant = _first(item, ("ccass_id", "participant_id", "broker_id", "broker_code"))
        shares = _first(
            item,
            ("holding_shares", "holding_quantity", "shares", "holding", "quantity", "volume"),
        )
        change = _first(item, ("change_shares", "change", "change_quantity", "holding_change"))
        if participant not in (None, "") and (shares not in (None, "") or change not in (None, "")):
            candidates.append(item)
    return candidates


def normalize_holdings(code: str, raw: Any, fallback_date: str = "") -> LongbridgeData:
    payload = _unwrap_tool_result(raw) if isinstance(raw, dict) else raw
    records = _record_list(payload)
    normalized = LongbridgeData(code=clean_stock_code(code))
    basis_candidates = []
    for record in records:
        ccass_id = str(_first(record, ("ccass_id", "participant_id", "broker_id", "broker_code"), "")).upper()
        if not PARTICIPANT_ID_RE.fullmatch(ccass_id):
            continue
        shares_value = _number(
            _first(record, ("holding_shares", "holding_quantity", "shares", "holding", "quantity", "volume"))
        )
        if shares_value is None:
            continue
        shares = int(round(shares_value))
        pct_issued = _percentage(record)
        data_date = _date(_first(record, ("data_date", "date", "trade_date", "holding_date"))) or fallback_date
        if data_date and not normalized.data_date:
            normalized.data_date = data_date
        if pct_issued and pct_issued > 0:
            basis_candidates.append(shares / (pct_issued / 100.0))
        normalized.holdings.append(
            {
                "data_date": data_date,
                "ccass_id": ccass_id,
                "participant_name": str(_first(record, ("participant_name", "broker_name", "name"), "")),
                "holding_shares": shares,
                "stake_pct_of_issued": pct_issued,
                "stake_pct_of_ccass": None,
                "source": "longbridge",
                "row_meaning": "Longbridge native holding percentage uses issued shares when a reliable basis is available.",
            }
        )
    total = sum(row["holding_shares"] for row in normalized.holdings)
    for row in normalized.holdings:
        row["stake_pct_of_ccass"] = round(row["holding_shares"] / total * 100, 6) if total else None
    if basis_candidates:
        ordered = sorted(basis_candidates)
        median = ordered[len(ordered) // 2]
        spread = (max(ordered) - min(ordered)) / median if median else 1
        if spread <= 0.03:
            normalized.issued_shares = int(round(median))
        else:
            normalized.warnings.append("Longbridge issued-share basis is inconsistent across holding rows; issued denominator withheld.")
    normalized.holdings.sort(key=lambda row: row["holding_shares"], reverse=True)
    if normalized.holdings:
        top5 = sum(row["holding_shares"] for row in normalized.holdings[:5])
        top10 = sum(row["holding_shares"] for row in normalized.holdings[:10])
        normalized.concentration = [
            {
                "data_date": normalized.data_date,
                "top5_pct_of_ccass": round(top5 / total * 100, 6) if total else None,
                "top10_pct_of_ccass": round(top10 / total * 100, 6) if total else None,
                "top5_pct_of_issued": round(top5 / normalized.issued_shares * 100, 6) if normalized.issued_shares else None,
                "top10_pct_of_issued": round(top10 / normalized.issued_shares * 100, 6) if normalized.issued_shares else None,
                "ccass_total_shares": total,
                "ccass_total_pct_of_issued": round(total / normalized.issued_shares * 100, 6) if normalized.issued_shares else None,
                "participant_count": len(normalized.holdings),
                "source": "longbridge",
            }
        ]
    return normalized


def derive_changes(current: list[dict[str, Any]], previous: list[dict[str, Any]], issued_shares: int | None) -> list[dict[str, Any]]:
    before = {row["ccass_id"]: int(row.get("holding_shares") or 0) for row in previous}
    current_ids = {row["ccass_id"] for row in current}
    rows = []
    for row in current:
        change = int(row.get("holding_shares") or 0) - before.get(row["ccass_id"], 0)
        if change == 0:
            continue
        rows.append(
            {
                "data_date": row.get("data_date", ""),
                "ccass_id": row["ccass_id"],
                "participant_name": row.get("participant_name", ""),
                "change_shares": change,
                "change_pct_of_issued": round(change / issued_shares * 100, 6) if issued_shares else None,
                "holding_after": row.get("holding_shares"),
                "source": "longbridge",
            }
        )
    for row in previous:
        if row["ccass_id"] in current_ids:
            continue
        change = -int(row.get("holding_shares") or 0)
        rows.append(
            {
                "data_date": current[0].get("data_date", "") if current else "",
                "ccass_id": row["ccass_id"],
                "participant_name": row.get("participant_name", ""),
                "change_shares": change,
                "change_pct_of_issued": round(change / issued_shares * 100, 6) if issued_shares else None,
                "holding_after": 0,
                "source": "longbridge",
            }
        )
    return sorted(rows, key=lambda row: abs(row["change_shares"]), reverse=True)


def normalize_changes(raw: Any, data_date: str, issued_shares: int | None) -> list[dict[str, Any]]:
    payload = _unwrap_tool_result(raw) if isinstance(raw, dict) else raw
    rows = []
    for record in _record_list(payload):
        ccass_id = str(_first(record, ("ccass_id", "participant_id", "broker_id", "broker_code"), "")).upper()
        change = _number(_first(record, ("change_shares", "change", "change_quantity", "holding_change")))
        if not PARTICIPANT_ID_RE.fullmatch(ccass_id) or change is None:
            continue
        rows.append(
            {
                "data_date": _date(_first(record, ("data_date", "date", "trade_date"))) or data_date,
                "ccass_id": ccass_id,
                "participant_name": str(_first(record, ("participant_name", "broker_name", "name"), "")),
                "change_shares": int(round(change)),
                "change_pct_of_issued": round(change / issued_shares * 100, 6) if issued_shares else None,
                "holding_after": _number(
                    _first(record, ("holding_shares", "holding_quantity", "shares", "holding", "quantity"))
                ),
                "source": "longbridge",
            }
        )
    return sorted(rows, key=lambda row: abs(row["change_shares"]), reverse=True)


def fetch_longbridge_stock(code: str, timeout: float = 20.0, path: Path = DB_PATH) -> LongbridgeData:
    token_payload = active_token_payload(timeout=timeout, path=path)
    if not token_payload or not token_payload.get("access_token"):
        raise LongbridgeAuthError("Longbridge is not authenticated.")
    client = LongbridgeMCPClient(token=str(token_payload["access_token"]), timeout=timeout)
    symbol = to_longbridge_symbol(code)
    raw = client.call_tool("broker_holding_detail", {"symbol": symbol})
    data = normalize_holdings(code, raw)
    data.tool_results["broker_holding_detail"] = raw
    if not data.holdings:
        raise LongbridgeError("Longbridge broker holding response contained no recognizable participant rows.")
    if not data.data_date:
        data.data_date = datetime.now(ZoneInfo("Asia/Hong_Kong")).date().isoformat()
        for row in data.holdings:
            row["data_date"] = data.data_date
        data.warnings.append("Longbridge response did not expose a data date; fetch date was used.")
    previous_dates = load_longbridge_snapshot_dates(code, limit=1, path=path)
    previous = load_longbridge_holdings(code, previous_dates[0], path=path) if previous_dates and previous_dates[0] != data.data_date else []
    upsert_longbridge_holdings(code, data.data_date, data.holdings, path=path)
    if previous:
        data.changes = derive_changes(data.holdings, previous, data.issued_shares)
        data.big_changes = [
            {**row, "threshold_used": "abs(change_shares / issued_shares) >= 0.25%"}
            for row in data.changes
            if data.issued_shares and abs(row["change_shares"]) / data.issued_shares >= 0.0025
        ]
    else:
        data.changes = normalize_changes(raw, data.data_date, data.issued_shares)
        if not data.changes:
            try:
                recent = client.call_tool("broker_holding", {"symbol": symbol, "period": "rct_1"})
                data.tool_results["broker_holding"] = recent
                data.changes = normalize_changes(recent, data.data_date, data.issued_shares)
            except LongbridgeError as exc:
                data.warnings.append(f"Longbridge one-day Changes unavailable: {exc}")
        data.warnings.append("Longbridge Big Changes unavailable until two distinct daily snapshots have been stored.")
    return data


def fetch_broker_daily(code: str, participant_id: str, days: int = 60, timeout: float = 20.0, path: Path = DB_PATH) -> list[dict[str, Any]]:
    participant = participant_id.upper()
    if not PARTICIPANT_ID_RE.fullmatch(participant):
        raise ValueError("participant_id must match ^[A-Ca-c]\\d{5}$")
    token_payload = active_token_payload(timeout=timeout, path=path)
    if not token_payload or not token_payload.get("access_token"):
        raise LongbridgeAuthError("Longbridge is not authenticated.")
    client = LongbridgeMCPClient(token=str(token_payload["access_token"]), timeout=timeout)
    raw = _unwrap_tool_result(client.call_tool("broker_holding_daily", {"symbol": to_longbridge_symbol(code), "broker_id": participant}))
    rows = []
    for item in _walk(raw):
        date_value = _date(_first(item, ("date", "data_date", "trade_date")))
        shares = _number(
            _first(item, ("holding_shares", "holding_quantity", "shares", "holding", "quantity"))
        )
        if date_value and shares is not None:
            rows.append(
                {
                    "date": date_value,
                    "holding_shares": int(round(shares)),
                    "stake_pct_of_issued": _percentage(item),
                    "change_shares": _number(_first(item, ("change_shares", "change", "change_quantity"))),
                    "source": "longbridge",
                }
            )
    rows.sort(key=lambda row: row["date"], reverse=True)
    return rows[: max(1, min(int(days), 60))]


def authenticate_agent_code(auth_code: str, timeout: float = 20.0, path: Path = DB_PATH) -> dict[str, Any]:
    code = str(auth_code or "").strip()
    if not code:
        raise ValueError("auth_code is required.")
    client = LongbridgeMCPClient(endpoint=AGENT_ENDPOINT, timeout=timeout)
    client.initialize()
    result = client._post("tools/call", {"name": "authenticate", "arguments": {"auth_code": code}})
    payload = _unwrap_tool_result(result)
    candidates = list(_walk(payload))
    token = next((str(item.get("access_token")) for item in candidates if item.get("access_token")), "")
    if not token:
        token = next((str(item.get("token")) for item in candidates if item.get("token")), "")
    if not token:
        message = str(payload.get("message") or "") if isinstance(payload, dict) else str(payload or "")
        match = re.search(r"(?:access[_ -]?token|bearer)\s*[:=]?\s*([A-Za-z0-9._~-]{24,})", message, flags=re.I)
        token = match.group(1) if match else ""
    if not token:
        raise LongbridgeAuthError("Longbridge authentication response did not include an access token.")
    expires_at = next((item.get("expires_at") for item in candidates if item.get("expires_at")), None)
    if not expires_at and token.count(".") == 2:
        try:
            encoded = token.split(".")[1]
            encoded += "=" * (-len(encoded) % 4)
            claims = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
            if claims.get("exp"):
                expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc).isoformat()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            pass
    stored = {
        "access_token": token,
        "expires_at": expires_at,
        "refresh_token": next((item.get("refresh_token") for item in candidates if item.get("refresh_token")), None),
        "authenticated_at": now_iso(),
        "auth_method": "agent_auth_code",
    }
    save_token_payload(stored, path=path)
    return {key: value for key, value in stored.items() if key != "access_token" and key != "refresh_token"}


def longbridge_health(path: Path = DB_PATH) -> dict[str, Any]:
    try:
        payload = load_token_payload(path=path)
    except LongbridgeAuthError as exc:
        return {"status": "invalid", "error": str(exc), "token_expires_at": None}
    if not payload:
        return {"status": "not_authenticated", "token_expires_at": None}
    expiry = _token_expiry(payload)
    remaining = int((expiry - datetime.now(timezone.utc)).total_seconds()) if expiry else None
    return {
        "status": "expired" if remaining is not None and remaining <= 0 else "authenticated",
        "token_expires_at": payload.get("expires_at"),
        "token_expires_in_seconds": remaining,
        "refresh_available": bool(payload.get("refresh_token")),
        "auth_method": payload.get("auth_method", "unknown"),
    }
