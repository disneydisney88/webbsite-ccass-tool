"""Google Drive delivery for persisted Round 4 research briefs.

The service-account credentials stay outside the repository. Uploads are
best-effort: the Turso brief remains the authoritative copy if Drive is not
configured or an upload fails.
"""

from __future__ import annotations

import io
import csv
import json
import os
from io import StringIO
from typing import Any, Iterable

from .fetcher import hkt_today


DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
SA_FILE_ENV = "GDRIVE_SA_FILE"
FOLDER_ID_ENV = "GDRIVE_FOLDER_ID"


def drive_config_status() -> dict[str, Any]:
    """Return non-secret configuration status for logs and job details."""

    sa_file = os.getenv(SA_FILE_ENV, "").strip()
    folder_id = os.getenv(FOLDER_ID_ENV, "").strip()
    return {
        "configured": bool(sa_file and folder_id),
        "service_account_file_set": bool(sa_file),
        "service_account_file_exists": bool(sa_file and os.path.isfile(sa_file)),
        "folder_id_set": bool(folder_id),
    }


def _drive_service() -> Any:
    """Build the Drive client lazily so local/offline runs need no SDK."""

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    sa_file = os.getenv(SA_FILE_ENV, "").strip()
    if not sa_file or not os.path.isfile(sa_file):
        raise RuntimeError(f"{SA_FILE_ENV} is missing or does not point to a file")
    credentials = service_account.Credentials.from_service_account_file(
        sa_file,
        scopes=[DRIVE_SCOPE],
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _upsert_drive_file(service: Any, folder_id: str, name: str, content: bytes, mime_type: str) -> dict[str, Any]:
    """Create or replace one named file in the configured folder."""

    from googleapiclient.http import MediaIoBaseUpload

    escaped_name = name.replace("'", "\\'")
    query = (
        f"'{folder_id}' in parents and name = '{escaped_name}' "
        "and trashed = false"
    )
    existing = service.files().list(
        q=query,
        spaces="drive",
        fields="files(id,name,webViewLink,parents)",
        pageSize=1,
    ).execute().get("files", [])
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=False)
    if existing:
        item = service.files().update(
            fileId=existing[0]["id"],
            media_body=media,
            fields="id,name,webViewLink,parents",
        ).execute()
    else:
        item = service.files().create(
            body={"name": name, "parents": [folder_id]},
            media_body=media,
            fields="id,name,webViewLink,parents",
        ).execute()
    return {
        "file_id": item.get("id"),
        "name": item.get("name", name),
        "url": item.get("webViewLink") or (
            f"https://drive.google.com/file/d/{item.get('id')}/view" if item.get("id") else ""
        ),
        "folder_id": folder_id,
    }


def brief_to_markdown(payload: dict[str, Any]) -> str:
    """Create a compact, factual Markdown companion for a JSON brief."""

    signals = payload.get("signals") or {}
    coverage = payload.get("coverage") or {}
    lines = [
        f"# CCASS research brief {payload.get('brief_date', '')}",
        "",
        f"- Data date: {payload.get('data_date', '')}",
        f"- Trade date covered: {payload.get('trade_date_covered', '')}",
        "",
        "## Coverage",
        "",
        "| Group | Count |",
        "| --- | ---: |",
    ]
    for group in ("lshape79", "caiji", "research", "fetched_ok", "fetched_fail"):
        if group in coverage:
            lines.append(f"| {group} | {coverage[group]} |")
    lines.extend(["", "## Signals", "", "| Signal | Rows |", "| --- | ---: |"])
    for key in ("S1", "S2", "S3", "S4_watchlist", "S4_market", "S5"):
        if key in signals:
            lines.append(f"| {key} | {len(signals[key] or [])} |")
    warnings = payload.get("data_quality") or []
    if warnings:
        lines.extend(["", "## Data quality", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines) + "\n"


HOLDINGS_CSV_FIELDS = (
    "code", "data_date", "ccass_id", "participant_name", "holding_shares",
    "stake_pct_of_issued", "stake_pct_of_ccass", "change_shares", "source", "fetched_at",
)


def holdings_to_csv(rows: Iterable[dict[str, Any]]) -> str:
    """Serialize daily holdings in the same long format as the panel API."""

    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=HOLDINGS_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows({field: row.get(field, "") for field in HOLDINGS_CSV_FIELDS} for row in rows)
    return output.getvalue()


def upload_brief_artifacts(
    payload: dict[str, Any],
    brief_date: str | None = None,
    holdings_rows: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Upsert the JSON, Markdown and optional daily holdings CSV."""

    status = drive_config_status()
    if not status["configured"]:
        return {"status": "not_configured", "config": status, "files": []}
    folder_id = os.getenv(FOLDER_ID_ENV, "").strip()
    artifact_date = brief_date or str(payload.get("brief_date") or hkt_today())
    compact_date = artifact_date.replace("-", "")
    stem = f"brief_{compact_date}"
    artifacts = [
        (f"{stem}.json", json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), "application/json"),
        (f"{stem}.md", brief_to_markdown(payload).encode("utf-8"), "text/markdown"),
    ]
    if holdings_rows is not None:
        artifacts.append(
            (
                f"holdings_daily_{compact_date}.csv",
                holdings_to_csv(holdings_rows).encode("utf-8-sig"),
                "text/csv",
            )
        )
    try:
        service = _drive_service()
    except Exception as exc:
        return {
            "status": "failed",
            "config": status,
            "files": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    uploaded: list[dict[str, Any]] = []
    errors: list[str] = []
    for name, content, mime_type in artifacts:
        try:
            uploaded.append(_upsert_drive_file(service, folder_id, name, content, mime_type))
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    return {
        "status": "success" if not errors else ("partial" if uploaded else "failed"),
        "config": status,
        "files": uploaded,
        **({"errors": errors} if errors else {}),
    }
