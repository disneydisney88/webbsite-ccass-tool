"""Turso/libSQL provisioning and health boundary for the migration."""

from __future__ import annotations

import os
from time import perf_counter
from typing import Any

TURSO_DATABASE_URL_ENV = "TURSO_DATABASE_URL"
TURSO_AUTH_TOKEN_ENV = "TURSO_AUTH_TOKEN"

TURSO_SCHEMA: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS snapshots (code TEXT NOT NULL, date TEXT NOT NULL, participant_id TEXT NOT NULL, participant_name TEXT NOT NULL, shares INTEGER NOT NULL, pct_of_issued REAL, source TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY (code, date, participant_id))",
    "CREATE TABLE IF NOT EXISTS stock_meta (code TEXT PRIMARY KEY, name TEXT, issued_shares TEXT, issued_shares_as_of TEXT)",
    "CREATE TABLE IF NOT EXISTS stock_map (code TEXT PRIMARY KEY, issue_id TEXT NOT NULL, name TEXT, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS price_history (code TEXT NOT NULL, date TEXT NOT NULL, close REAL, open REAL, high REAL, low REAL, volume INTEGER, turnover REAL, vwap REAL, price_source TEXT NOT NULL, turnover_est REAL, fetched_at TEXT NOT NULL, PRIMARY KEY (code, date, price_source))",
    "CREATE TABLE IF NOT EXISTS mirror_probe (probe_date TEXT NOT NULL, mirror_base_url TEXT NOT NULL, status TEXT NOT NULL, browser_sections TEXT NOT NULL, error_message TEXT NOT NULL DEFAULT '', probed_at TEXT NOT NULL, PRIMARY KEY (probe_date, mirror_base_url))",
    "CREATE TABLE IF NOT EXISTS longbridge_holdings_daily (code TEXT NOT NULL, data_date TEXT NOT NULL, ccass_id TEXT NOT NULL, participant_name TEXT NOT NULL, holding_shares INTEGER NOT NULL, stake_pct_of_issued REAL, change_shares REAL, fetched_at TEXT NOT NULL, PRIMARY KEY (code, data_date, ccass_id))",
    "CREATE TABLE IF NOT EXISTS holdings_daily (code TEXT NOT NULL, data_date TEXT NOT NULL, ccass_id TEXT NOT NULL, participant_name TEXT NOT NULL, holding_shares INTEGER NOT NULL, stake_pct_of_issued REAL, stake_pct_of_ccass REAL, change_shares REAL, source TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY (code, data_date, ccass_id))",
    "CREATE TABLE IF NOT EXISTS longbridge_credentials (credential_id TEXT PRIMARY KEY, encrypted_payload BLOB NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS watchlist (group_name TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', tag TEXT NOT NULL DEFAULT '', priority INTEGER, added_date TEXT, source TEXT NOT NULL DEFAULT '', PRIMARY KEY (group_name, code))",
    "CREATE TABLE IF NOT EXISTS job_log (job_id TEXT PRIMARY KEY, job_type TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, detail_json TEXT NOT NULL DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS quote_daily (code TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL, turnover REAL, volume INTEGER, market_cap REAL, fetched_at TEXT NOT NULL, PRIMARY KEY (code, trade_date))",
    "CREATE TABLE IF NOT EXISTS hypotheses (id TEXT PRIMARY KEY, code TEXT NOT NULL, created TEXT NOT NULL, expected_date TEXT NOT NULL, ccass_id TEXT NOT NULL, field TEXT NOT NULL, op TEXT NOT NULL, value TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, resolved_date TEXT, actual TEXT)",
    "CREATE TABLE IF NOT EXISTS briefs (brief_date TEXT PRIMARY KEY, data_date TEXT NOT NULL, trade_date_covered TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)",
)


def turso_is_configured() -> bool:
    return bool(os.getenv(TURSO_DATABASE_URL_ENV, "").strip() and os.getenv(TURSO_AUTH_TOKEN_ENV, "").strip())


def _create_client():
    try:
        import libsql_client
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("libsql-client is not installed") from exc
    url = os.getenv(TURSO_DATABASE_URL_ENV, "").strip()
    token = os.getenv(TURSO_AUTH_TOKEN_ENV, "").strip()
    if not url or not token:
        raise RuntimeError("TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are required")
    return libsql_client.create_client_sync(url, auth_token=token)


def ensure_turso_schema() -> None:
    with _create_client() as client:
        for statement in TURSO_SCHEMA:
            client.execute(statement)


def turso_health() -> dict[str, Any]:
    if not turso_is_configured():
        return {"db_backend": "sqlite", "turso_ping_ms": None}
    started = perf_counter()
    try:
        ensure_turso_schema()
        with _create_client() as client:
            client.execute("SELECT 1")
    except Exception as exc:  # health must remain available on backend failure
        return {"db_backend": "turso", "turso_ping_ms": None, "turso_error": f"{type(exc).__name__}: {exc}"}
    return {"db_backend": "turso", "turso_ping_ms": round((perf_counter() - started) * 1000, 1)}
