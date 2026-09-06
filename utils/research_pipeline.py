"""Round 4 research pipeline primitives.

The module keeps the worker, timeline, signal and brief contracts in one place.
It uses Turso for the application database when configured and retains the
existing SQLite path for local development and fixture tests.
"""

from __future__ import annotations

import json
import csv
import sqlite3
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .date_semantics import shift_trading_date
from .fetcher import clean_stock_code, now_iso
from .longbridge import LongbridgeAuthError, LongbridgeError, fetch_longbridge_stock
from .google_drive import upload_brief_artifacts
from .snapshot_db import DB_PATH, ensure_db, load_watchlist_entries
from .turso_db import ensure_turso_schema, turso_execute, turso_query, turso_is_configured


GROUP_ORDER = ("lshape79", "caiji", "research")
DEFAULT_DAILY_GROUPS = ("lshape79", "caiji")
RESEARCH_BATCH_SIZE = 100
RESEARCH_BATCH_PAUSE_SECONDS = 60.0


def _ensure_local_round4(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS holdings_daily (
          code TEXT NOT NULL, data_date TEXT NOT NULL, ccass_id TEXT NOT NULL,
          participant_name TEXT NOT NULL, holding_shares INTEGER NOT NULL,
          stake_pct_of_issued REAL, stake_pct_of_ccass REAL, change_shares REAL,
          source TEXT NOT NULL, fetched_at TEXT NOT NULL,
          PRIMARY KEY (code, data_date, ccass_id)
        );
        CREATE TABLE IF NOT EXISTS job_log (
          job_id TEXT PRIMARY KEY, job_type TEXT NOT NULL, status TEXT NOT NULL,
          started_at TEXT NOT NULL, finished_at TEXT, detail_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS quote_daily (
          code TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL, turnover REAL,
          volume INTEGER, market_cap REAL, fetched_at TEXT NOT NULL,
          PRIMARY KEY (code, trade_date)
        );
        CREATE TABLE IF NOT EXISTS hypotheses (
          id TEXT PRIMARY KEY, code TEXT NOT NULL, created TEXT NOT NULL, expected_date TEXT NOT NULL,
          ccass_id TEXT NOT NULL, field TEXT NOT NULL, op TEXT NOT NULL, value TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, resolved_date TEXT, actual TEXT
        );
        CREATE TABLE IF NOT EXISTS briefs (
          brief_date TEXT PRIMARY KEY, data_date TEXT NOT NULL, trade_date_covered TEXT NOT NULL,
          payload_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """
    )


def _remote(path: Path = DB_PATH) -> bool:
    return Path(path) == DB_PATH and turso_is_configured()


def _query(sql: str, args: tuple[Any, ...] = (), path: Path = DB_PATH) -> list[dict[str, Any]]:
    if _remote(path):
        ensure_turso_schema()
        return turso_query(sql, args)
    ensure_db(path)
    with sqlite3.connect(path) as conn:
        _ensure_local_round4(conn)
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, args).fetchall()]


def _execute(sql: str, args: tuple[Any, ...] = (), path: Path = DB_PATH) -> None:
    if _remote(path):
        ensure_turso_schema()
        turso_execute(sql, args)
        return
    ensure_db(path)
    with sqlite3.connect(path) as conn:
        _ensure_local_round4(conn)
        conn.execute(sql, args)
        conn.commit()


def _execute_many(sql: str, rows: list[tuple[Any, ...]], path: Path = DB_PATH) -> None:
    if not rows:
        return
    if _remote(path):
        ensure_turso_schema()
        from .turso_db import turso_execute_many

        turso_execute_many(sql, rows)
        return
    ensure_db(path)
    with sqlite3.connect(path) as conn:
        _ensure_local_round4(conn)
        conn.executemany(sql, rows)
        conn.commit()


def hkt_today() -> str:
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).date().isoformat()


def get_job(job_id: str, path: Path = DB_PATH) -> dict[str, Any] | None:
    rows = _query("SELECT * FROM job_log WHERE job_id=?", (job_id,), path)
    if not rows:
        return None
    row = rows[0]
    try:
        row["detail"] = json.loads(row.pop("detail_json") or "{}")
    except (TypeError, ValueError):
        row["detail"] = {}
    return row


def start_job(
    job_id: str,
    job_type: str = "daily",
    path: Path = DB_PATH,
    initial_detail: dict[str, Any] | None = None,
    force: bool = False,
) -> tuple[dict[str, Any], bool]:
    existing = get_job(job_id, path)
    if existing and existing.get("status") in {"queued", "running", "succeeded"}:
        if not force or existing.get("status") in {"queued", "running"}:
            return existing, False
        _execute("DELETE FROM job_log WHERE job_id=?", (job_id,), path)
    started = now_iso()
    detail_json = json.dumps(initial_detail or {}, ensure_ascii=False)
    _execute(
        """INSERT INTO job_log(job_id, job_type, status, started_at, finished_at, detail_json)
           VALUES (?, ?, 'queued', ?, NULL, ?)
           ON CONFLICT(job_id) DO UPDATE SET job_type=excluded.job_type,
           status='queued', started_at=excluded.started_at, finished_at=NULL,
           detail_json=excluded.detail_json""",
        (job_id, job_type, started, detail_json),
        path,
    )
    return get_job(job_id, path) or {"job_id": job_id, "status": "queued"}, True


def _set_job(job_id: str, status: str, detail: dict[str, Any], path: Path = DB_PATH) -> None:
    finished = now_iso() if status in {"succeeded", "failed", "cancelled"} else None
    _execute(
        "UPDATE job_log SET status=?, finished_at=?, detail_json=? WHERE job_id=?",
        (status, finished, json.dumps(detail, ensure_ascii=False), job_id),
        path,
    )


def _daily_entries(groups: tuple[str, ...] | list[str] | None = None) -> list[Any]:
    entries: list[Any] = []
    seen: set[str] = set()
    selected_groups = tuple(groups or DEFAULT_DAILY_GROUPS)
    for group in selected_groups:
        group_entries = load_watchlist_entries(group=group)
        group_entries.sort(key=lambda item: (item.priority if item.priority is not None else 99, item.code))
        for entry in group_entries:
            if entry.code not in seen:
                seen.add(entry.code)
                entries.append(entry)
    return entries


def daily_entry_count(groups: tuple[str, ...] | list[str] | None = None) -> int:
    return len(_daily_entries(groups))


def latest_longbridge_data_date(
    groups: tuple[str, ...] | list[str] | None = None,
    path: Path = DB_PATH,
) -> str:
    """Return the latest stored Longbridge data date for selected groups."""
    codes = sorted({entry.code for entry in _daily_entries(groups)})
    if not codes:
        return ""
    placeholders = ",".join("?" for _ in codes)
    rows = _query(
        f"SELECT MAX(data_date) AS data_date FROM longbridge_holdings_daily "
        f"WHERE code IN ({placeholders})",
        tuple(codes),
        path,
    )
    return str(rows[0].get("data_date") or "") if rows else ""


def run_daily_job(
    job_id: str,
    sleep_seconds: float = 1.5,
    fetcher: Callable[..., Any] = fetch_longbridge_stock,
    path: Path = DB_PATH,
    groups: tuple[str, ...] | list[str] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    test_mode: bool = False,
) -> dict[str, Any]:
    """Fetch selected watchlist groups and persist progress after every stock."""

    selected_groups = tuple(groups or DEFAULT_DAILY_GROUPS)
    entries = _daily_entries(selected_groups)
    started_at = now_iso()
    started_monotonic = time.monotonic()
    rows: list[dict[str, Any]] = []
    detail: dict[str, Any] = {
        "groups": list(selected_groups),
        "total": len(entries),
        "succeeded": 0,
        "skipped": 0,
        "failed": 0,
        "current_code": "",
        "elapsed_s": 0.0,
        "started": started_at,
        "test": bool(test_mode),
        "stage": "fetch",
        "stage_elapsed_s": 0.0,
        "stages": {},
        "results": rows,
    }

    def update(status: str = "running", current_code: str = "") -> None:
        detail["current_code"] = current_code
        detail["succeeded"] = sum(1 for row in rows if row.get("ok") and not row.get("skipped"))
        detail["skipped"] = sum(1 for row in rows if row.get("skipped"))
        detail["failed"] = sum(1 for row in rows if not row.get("ok") and not row.get("skipped"))
        detail["elapsed_s"] = round(time.monotonic() - started_monotonic, 3)
        _set_job(job_id, status, detail, path)

    def record_stage(stage: str, stage_elapsed_s: float, status: str = "succeeded") -> None:
        detail["stage"] = stage
        detail["stage_elapsed_s"] = round(stage_elapsed_s, 3)
        detail.setdefault("stages", {})[stage] = {
            "status": status,
            "stage_elapsed_s": round(stage_elapsed_s, 3),
        }
        update()

    if cancel_requested and cancel_requested():
        update("cancelled")
        return get_job(job_id, path) or {"job_id": job_id, **detail}
    update()

    for index, entry in enumerate(entries):
        if cancel_requested and cancel_requested():
            update("cancelled")
            return get_job(job_id, path) or {"job_id": job_id, **detail}
        if index:
            if "research" in selected_groups and index % RESEARCH_BATCH_SIZE == 0:
                time.sleep(RESEARCH_BATCH_PAUSE_SECONDS)
            time.sleep(max(0.0, sleep_seconds))
        stock_started = time.monotonic()
        update(current_code=entry.code)
        try:
            data = fetcher(entry.code, timeout=30.0, path=path)
            row = {
                "code": entry.code,
                "group": ";".join(entry.groups),
                "ok": True,
                "data_date": data.data_date,
                "participant_count": len(data.holdings),
                "warnings": list(data.warnings),
            }
            row["elapsed_s"] = round(time.monotonic() - stock_started, 3)
            rows.append(row)
        except LongbridgeAuthError as exc:
            rows.append({"code": entry.code, "group": ";".join(entry.groups), "ok": False,
                         "error_type": "LONGBRIDGE_AUTH_EXPIRED", "error": str(exc),
                         "elapsed_s": round(time.monotonic() - stock_started, 3)})
            update()
            break
        except (LongbridgeError, Exception) as exc:  # individual stock failure is non-fatal
            rows.append({"code": entry.code, "group": ";".join(entry.groups), "ok": False,
                         "error_type": type(exc).__name__, "error": str(exc),
                         "elapsed_s": round(time.monotonic() - stock_started, 3)})
        update()
    detail = {
        **detail,
        "total": len(entries),
        "succeeded": sum(1 for row in rows if row.get("ok") and not row.get("skipped")),
        "skipped": sum(1 for row in rows if row.get("skipped")),
        "failed": sum(1 for row in rows if not row.get("ok") and not row.get("skipped")),
        "rate_limit_429": sum(1 for row in rows if row.get("error_type") == "HTTP_429"),
    }
    detail["current_code"] = ""
    detail["elapsed_s"] = round(time.monotonic() - started_monotonic, 3)
    detail["longbridge_data_date"] = max(
        (str(row.get("data_date")) for row in rows if row.get("ok") and row.get("data_date")),
        default="",
    )
    successful_dates = [str(row.get("data_date")) for row in rows if row.get("ok") and row.get("data_date")]
    try:
        brief_date = max(successful_dates) if successful_dates else hkt_today()
        detail["brief"] = build_brief(
            brief_date,
            path,
            fetch_stats={"fetched_ok": detail["succeeded"], "fetched_fail": detail["failed"]},
            stage_callback=record_stage,
            persist=not test_mode,
            upload=not test_mode,
        )
    except Exception as exc:
        detail["brief_error"] = f"{type(exc).__name__}: {exc}"
    detail["stage"] = "complete"
    detail["stage_elapsed_s"] = detail["elapsed_s"]
    _set_job(job_id, "succeeded" if detail["failed"] == 0 else "failed", detail, path)
    return get_job(job_id, path) or {"job_id": job_id, **detail}


def cancel_job(job_id: str, path: Path = DB_PATH) -> dict[str, Any] | None:
    """Mark a queued/running job for cooperative cancellation."""
    job = get_job(job_id, path)
    if job is None:
        return None
    if job.get("status") in {"succeeded", "failed", "cancelled"}:
        return job
    detail = dict(job.get("detail") or {})
    detail["cancel_requested"] = True
    _set_job(job_id, "cancelling", detail, path)
    return get_job(job_id, path)


def delete_job(job_id: str, path: Path = DB_PATH) -> bool | None:
    """Delete a terminal job record after any cooperative cancellation has settled."""
    job = get_job(job_id, path)
    if job is None:
        return None
    if job.get("status") not in {"succeeded", "failed", "cancelled"}:
        return False
    _execute("DELETE FROM job_log WHERE job_id=?", (job_id,), path)
    return True


def job_cancel_requested(job_id: str, path: Path = DB_PATH) -> bool:
    job = get_job(job_id, path)
    if not job:
        return False
    return job.get("status") in {"cancelling", "cancelled"} or bool(
        (job.get("detail") or {}).get("cancel_requested")
    )


def _holdings_rows(code: str, from_date: str = "", to_date: str = "", path: Path = DB_PATH) -> list[dict[str, Any]]:
    normalized = clean_stock_code(code)
    clauses = ["code=?"]
    args: list[Any] = [normalized]
    if from_date:
        clauses.append("data_date>=?")
        args.append(from_date)
    if to_date:
        clauses.append("data_date<=?")
        args.append(to_date)
    return _query(
        "SELECT code,data_date,ccass_id,participant_name,holding_shares,stake_pct_of_issued,"
        "stake_pct_of_ccass,change_shares,source,fetched_at FROM holdings_daily WHERE "
        + " AND ".join(clauses) + " ORDER BY data_date, holding_shares DESC, ccass_id",
        tuple(args), path,
    )


def _timeline_from_rows(code: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[str(row["data_date"])].append(row)
    timeline: list[dict[str, Any]] = []
    for data_date, day_rows in sorted(by_date.items()):
        ordered = sorted(day_rows, key=lambda row: int(row.get("holding_shares") or 0), reverse=True)
        total = sum(int(row.get("holding_shares") or 0) for row in ordered)
        top5 = sum(int(row.get("holding_shares") or 0) for row in ordered[:5])
        top10 = sum(int(row.get("holding_shares") or 0) for row in ordered[:10])
        sources = sorted({str(row.get("source") or "") for row in ordered if row.get("source")})
        timeline.append({
            "date": data_date,
            "ccass_total_shares": total,
            "ccass_total_pct_issued": _sum_pct(ordered, "stake_pct_of_issued"),
            "top5_pct_ccass": round(top5 / total * 100, 6) if total else None,
            "top5_pct_issued": _sum_pct(ordered[:5], "stake_pct_of_issued"),
            "top10_pct_ccass": round(top10 / total * 100, 6) if total else None,
            "top10_pct_issued": _sum_pct(ordered[:10], "stake_pct_of_issued"),
            "participant_count": len(ordered),
            "top1_id": ordered[0].get("ccass_id", "") if ordered else "",
            "top1_shares": int(ordered[0].get("holding_shares") or 0) if ordered else 0,
            "source": sources[0] if len(sources) == 1 else "both" if sources else "",
        })
    return timeline


def build_timeline(code: str, from_date: str = "", to_date: str = "", path: Path = DB_PATH) -> list[dict[str, Any]]:
    return _timeline_from_rows(code, _holdings_rows(code, from_date, to_date, path))


def _sum_pct(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [float(row[field]) for row in rows if row.get(field) not in (None, "")]
    return round(sum(values), 6) if values else None


def build_broker_panel(group: str, from_date: str = "", to_date: str = "", path: Path = DB_PATH) -> list[dict[str, Any]]:
    entries = load_watchlist_entries(group=group)
    codes = [entry.code for entry in entries]
    if not codes:
        return []
    placeholders = ",".join("?" for _ in codes)
    clauses = [f"code IN ({placeholders})"]
    args: list[Any] = list(codes)
    if from_date:
        clauses.append("data_date>=?"); args.append(from_date)
    if to_date:
        clauses.append("data_date<=?"); args.append(to_date)
    return _query(
        "SELECT code,data_date,ccass_id,participant_name,holding_shares,stake_pct_of_issued,"
        "stake_pct_of_ccass,change_shares,source FROM holdings_daily WHERE " + " AND ".join(clauses)
        + " ORDER BY code,data_date,holding_shares DESC,ccass_id", tuple(args), path,
    )


def build_transfer_candidates(group: str, from_date: str = "", to_date: str = "", path: Path = DB_PATH) -> list[dict[str, Any]]:
    rows = build_broker_panel(group, from_date, to_date, path)
    by_day: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("change_shares") not in (None, "", 0):
            by_day[(str(row["code"]), str(row["data_date"]))].append(row)
    result: list[dict[str, Any]] = []
    for (code, data_date), day_rows in by_day.items():
        negatives = [row for row in day_rows if float(row.get("change_shares") or 0) < 0]
        positives = [row for row in day_rows if float(row.get("change_shares") or 0) > 0]
        for source in negatives:
            loss = abs(float(source["change_shares"]))
            for target in positives:
                gain = float(target["change_shares"])
                shares = min(loss, gain)
                issued = _issued_shares(code, path)
                if issued and shares / issued < 0.005:
                    continue
                if abs(loss - gain) > max(loss, gain) * 0.02:
                    continue
                quote = _query("SELECT volume FROM quote_daily WHERE code=? AND trade_date=?", (code, data_date), path)
                volume = quote[0].get("volume") if quote else None
                if volume is not None and float(volume) >= shares:
                    continue
                result.append({"code": code, "date": data_date, "from_id": source["ccass_id"],
                               "to_id": target["ccass_id"], "shares": int(round(shares)),
                               "volume_that_day": volume, "ratio": round(shares / loss, 6) if loss else None})
    return result


def _issued_shares(code: str, path: Path = DB_PATH) -> int | None:
    rows = _query("SELECT issued_shares FROM stock_meta WHERE code=?", (clean_stock_code(code),), path)
    if not rows:
        return None
    try:
        return int(float(str(rows[0].get("issued_shares") or "").replace(",", "")))
    except (TypeError, ValueError):
        return None


def add_hypothesis(item: dict[str, Any], path: Path = DB_PATH) -> dict[str, Any]:
    hypothesis_id = str(item.get("id") or uuid.uuid4())
    _execute(
        """INSERT INTO hypotheses(id,code,created,expected_date,ccass_id,field,op,value,note,status,resolved_date,actual)
           VALUES (?,?,?,?,?,?,?,?,?,'pending',NULL,NULL)
           ON CONFLICT(id) DO UPDATE SET note=excluded.note, expected_date=excluded.expected_date""",
        (hypothesis_id, clean_stock_code(str(item.get("code") or "")), str(item.get("created") or hkt_today()),
         str(item["expected_date"]), str(item["ccass_id"]), str(item.get("field") or "holding_shares"),
         str(item.get("op") or "<="), str(item["value"]), str(item.get("note") or "")), path,
    )
    return get_hypotheses(hypothesis_id=hypothesis_id, path=path)[0]


def get_hypotheses(status: str | None = None, hypothesis_id: str | None = None, path: Path = DB_PATH) -> list[dict[str, Any]]:
    clauses: list[str] = []
    args: list[Any] = []
    if status:
        clauses.append("status=?"); args.append(status)
    if hypothesis_id:
        clauses.append("id=?"); args.append(hypothesis_id)
    return _query("SELECT * FROM hypotheses" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY expected_date,id", tuple(args), path)


def resolve_hypotheses(as_of: str, path: Path = DB_PATH) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for item in get_hypotheses(status="pending", path=path):
        if str(item["expected_date"]) > as_of:
            continue
        rows = _query("SELECT holding_shares FROM holdings_daily WHERE code=? AND ccass_id=? AND data_date<=? ORDER BY data_date DESC LIMIT 1",
                      (item["code"], item["ccass_id"], as_of), path)
        if not rows:
            continue
        actual = rows[0].get("holding_shares")
        try:
            expected = float(item["value"])
            actual_num = float(actual)
            op = item["op"]
            hit = {"<=": actual_num <= expected, "<": actual_num < expected, ">=": actual_num >= expected,
                   ">": actual_num > expected, "=": actual_num == expected}.get(op, False)
        except (TypeError, ValueError):
            hit = False
        _execute("UPDATE hypotheses SET status=?,resolved_date=?,actual=? WHERE id=?",
                 ("hit" if hit else "miss", as_of, str(actual), item["id"]), path)
        resolved.extend(get_hypotheses(hypothesis_id=item["id"], path=path))
    return resolved


def build_brief(
    brief_date: str,
    path: Path = DB_PATH,
    fetch_stats: dict[str, int] | None = None,
    stage_callback: Callable[[str, float, str], None] | None = None,
    persist: bool = True,
    upload: bool = True,
) -> dict[str, Any]:
    started_monotonic = time.monotonic()

    def emit(stage: str, started: float, status: str = "succeeded") -> None:
        if stage_callback:
            stage_callback(stage, time.monotonic() - started, status)

    entries_by_group = {group: load_watchlist_entries(group=group) for group in GROUP_ORDER}
    timeline_codes = {entry.code for entries in entries_by_group.values() for entry in entries}
    codes = sorted(timeline_codes)
    s1: list[dict[str, Any]] = []
    s2: list[dict[str, Any]] = []
    s3: list[dict[str, Any]] = []
    s4_watchlist: list[dict[str, Any]] = []
    s4_market: list[dict[str, Any]] = []
    daily_holdings_rows: list[dict[str, Any]] = []

    # Load all holdings and issued-share metadata once. The previous per-code
    # queries made a brief over a large watchlist spend most of its time on
    # Turso round trips.
    stage_started = time.monotonic()
    rows_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    issued_by_code: dict[str, int | None] = {}
    if codes:
        placeholders = ",".join("?" for _ in codes)
        all_rows = _query(
            "SELECT code,data_date,ccass_id,participant_name,holding_shares,stake_pct_of_issued,"
            "stake_pct_of_ccass,change_shares,source,fetched_at FROM holdings_daily "
            f"WHERE code IN ({placeholders}) AND data_date<=? "
            "ORDER BY code,data_date,holding_shares DESC,ccass_id",
            tuple(codes) + (brief_date,), path,
        )
        meta_rows = _query(
            f"SELECT code,issued_shares FROM stock_meta WHERE code IN ({placeholders})",
            tuple(codes), path,
        )
        for row in all_rows:
            rows_by_code[str(row.get("code") or "")].append(row)
        for row in meta_rows:
            try:
                issued_by_code[str(row.get("code") or "")] = int(float(str(row.get("issued_shares") or "").replace(",", "")))
            except (TypeError, ValueError):
                issued_by_code[str(row.get("code") or "")] = None
        daily_holdings_rows.extend(
            row for row in all_rows if str(row.get("data_date") or "") == brief_date
        )
    emit("S1_load", stage_started)

    stage_started = time.monotonic()
    for code in codes:
        rows = rows_by_code.get(code, [])
        issued = issued_by_code.get(code)
        seen_ids: set[str] = set()
        for row in rows:
            pct = row.get("stake_pct_of_issued")
            change = row.get("change_shares")
            ccass_id = str(row.get("ccass_id") or "")
            holding = _as_float(row.get("holding_shares"))
            if issued and ccass_id and ccass_id not in seen_ids and holding is not None:
                if holding / issued >= 0.005:
                    s2.append({
                        "code": code,
                        "ccass_id": ccass_id,
                        "name": row["participant_name"],
                        "change_shares": change,
                        "pct": round(holding / issued * 100, 6),
                        "holding_after": row["holding_shares"],
                        "first_seen": row["data_date"],
                    })
            seen_ids.add(ccass_id)
    emit("S2", stage_started)

    stage_started = time.monotonic()
    for code in codes:
        rows = rows_by_code.get(code, [])
        issued = issued_by_code.get(code)
        for row in rows:
            change = _as_float(row.get("change_shares"))
            pct = row.get("stake_pct_of_issued")
            if issued and change and pct not in (None, ""):
                if abs(change) / issued >= 0.01:
                    s1.append({"code": code, "ccass_id": row["ccass_id"], "name": row["participant_name"],
                               "change_shares": change, "pct": round(abs(change) / issued * 100, 6),
                               "holding_after": row["holding_shares"]})
    emit("S1", stage_started)

    stage_started = time.monotonic()
    for code in codes:
        current_timeline = _timeline_from_rows(code, rows_by_code.get(code, []))
        for before, after in zip(current_timeline, current_timeline[1:]):
            before_top5 = before.get("top5_pct_ccass")
            after_top5 = after.get("top5_pct_ccass")
            if before_top5 is not None and after_top5 is not None and abs(after_top5 - before_top5) >= 2:
                s3.append({"code": code, "date": after["date"], "top5_before": before_top5, "top5_after": after_top5})
    emit("S3", stage_started)

    # Quote rows are populated by a permitted market-data collector. Keep the
    # computation separate from holdings so missing quote coverage is visible.
    stage_started = time.monotonic()
    quote_rows = _query(
        "SELECT code,trade_date,turnover,market_cap FROM quote_daily WHERE trade_date<=? ORDER BY code,trade_date DESC",
        (brief_date,), path,
    )
    quote_rows_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for quote in quote_rows:
        quote_rows_by_code[str(quote.get("code") or "")].append(quote)
    for code, code_quotes in quote_rows_by_code.items():
        for index, quote in enumerate(code_quotes):
            turnover = _as_float(quote.get("turnover"))
            if turnover is None or turnover < 3_500_000:
                continue
            median = _median([_as_float(row.get("turnover")) for row in code_quotes[index + 1:index + 21]])
            if median and turnover / median >= 3:
                item = {"code": code, "trade_date": quote["trade_date"], "turnover": turnover,
                        "ratio": round(turnover / median, 6),
                        "turnover_to_mcap": _ratio(turnover, _as_float(quote.get("market_cap")))}
                (s4_watchlist if code in timeline_codes else s4_market).append(item)
    emit("S4", stage_started)

    stage_started = time.monotonic()
    s5 = _event_signals(brief_date, timeline_codes)
    emit("S5", stage_started)

    stage_started = time.monotonic()
    hypotheses = resolve_hypotheses(brief_date, path)
    emit("hypotheses", stage_started)

    # Transfer candidates remain an on-demand panel calculation; recording the
    # stage keeps the job detail explicit without re-running its heavy query.
    emit("transfers", time.monotonic(), "not_materialized_in_brief")
    trade_date_covered, trade_date_warning = shift_trading_date(brief_date, -2)
    data_quality = []
    if trade_date_warning:
        data_quality.append(trade_date_warning)
    payload = {
        "brief_date": brief_date,
        "data_date": brief_date,
        "trade_date_covered": trade_date_covered,
        "coverage": {
            **{group: len(entries_by_group[group]) for group in GROUP_ORDER},
            **(fetch_stats or {}),
        },
        "signals": {"S1": s1, "S2": s2, "S3": s3, "S4_watchlist": s4_watchlist, "S4_market": s4_market, "S5": s5},
        "hypotheses_resolved": hypotheses,
        "hypotheses_pending_next3d": [item for item in get_hypotheses(status="pending", path=path)
                                      if str(item["expected_date"]) <= (date.fromisoformat(brief_date) + timedelta(days=3)).isoformat()],
        "data_quality": data_quality,
    }
    # Drive is a delivery copy; Turso remains authoritative when Drive is
    # unavailable or the service account lacks access to the target folder.
    stage_started = time.monotonic()
    if upload:
        payload["drive_upload"] = upload_brief_artifacts(payload, brief_date, daily_holdings_rows)
        emit("drive_upload", stage_started)
    else:
        payload["drive_upload"] = {"status": "skipped_test", "files": []}
        emit("drive_upload", stage_started, "skipped_test")
    if persist:
        _execute(
            """INSERT INTO briefs(brief_date,data_date,trade_date_covered,payload_json,created_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(brief_date) DO UPDATE SET payload_json=excluded.payload_json,
               created_at=excluded.created_at""",
                 (brief_date, brief_date, "", json.dumps(payload, ensure_ascii=False), now_iso()), path)
    return payload


def _as_float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _median(values: list[float | None]) -> float | None:
    numbers = sorted(value for value in values if value is not None)
    if not numbers:
        return None
    middle = len(numbers) // 2
    return numbers[middle] if len(numbers) % 2 else (numbers[middle - 1] + numbers[middle]) / 2


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    return round(numerator / denominator, 6) if numerator is not None and denominator else None


def _event_signals(brief_date: str, codes: set[str], event_dir: Path | None = None) -> list[dict[str, Any]]:
    """Return event dates falling within the next three calendar weekdays."""
    root = event_dir or Path(__file__).parent / "../data/events"
    try:
        start = date.fromisoformat(brief_date)
    except ValueError:
        return []
    target_dates = {(start + timedelta(days=offset)).isoformat() for offset in range(4)}
    results: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.csv")) if root.exists() else []:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    raw_code = str(row.get("代號") or row.get("code") or "").split(".")[0].zfill(5)
                    if raw_code not in codes:
                        continue
                    for field, value in row.items():
                        parsed = _event_date(value)
                        if parsed in target_dates:
                            results.append({"code": raw_code, "event_type": path.stem,
                                            "date_field": field, "date": parsed})
        except (OSError, UnicodeError):
            continue
    return results


def _event_date(value: Any) -> str:
    text = str(value or "").strip().replace("/", "-")
    for fmt in ("%d-%m-%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def load_brief(brief_date: str = "", path: Path = DB_PATH) -> dict[str, Any] | None:
    rows = _query("SELECT payload_json FROM briefs WHERE brief_date=?" if brief_date else "SELECT payload_json FROM briefs ORDER BY brief_date DESC LIMIT 1",
                  (brief_date,) if brief_date else (), path)
    if not rows:
        return None
    try:
        return json.loads(rows[0]["payload_json"])
    except (TypeError, ValueError):
        return None
