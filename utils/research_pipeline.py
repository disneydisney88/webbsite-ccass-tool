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
from .snapshot_db import DB_PATH, ensure_db, load_watchlist_entries
from .turso_db import ensure_turso_schema, turso_execute, turso_query, turso_is_configured


GROUP_ORDER = ("lshape79", "caiji", "research")


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


def start_job(job_id: str, job_type: str = "daily", path: Path = DB_PATH) -> tuple[dict[str, Any], bool]:
    existing = get_job(job_id, path)
    if existing and existing.get("status") in {"queued", "running", "succeeded"}:
        return existing, False
    started = now_iso()
    _execute(
        """INSERT INTO job_log(job_id, job_type, status, started_at, finished_at, detail_json)
           VALUES (?, ?, 'queued', ?, NULL, '{}')
           ON CONFLICT(job_id) DO UPDATE SET job_type=excluded.job_type,
           status='queued', started_at=excluded.started_at, finished_at=NULL,
           detail_json='{}'""",
        (job_id, job_type, started),
        path,
    )
    return get_job(job_id, path) or {"job_id": job_id, "status": "queued"}, True


def _set_job(job_id: str, status: str, detail: dict[str, Any], path: Path = DB_PATH) -> None:
    finished = now_iso() if status in {"succeeded", "failed"} else None
    _execute(
        "UPDATE job_log SET status=?, finished_at=?, detail_json=? WHERE job_id=?",
        (status, finished, json.dumps(detail, ensure_ascii=False), job_id),
        path,
    )


def _daily_entries() -> list[Any]:
    entries: list[Any] = []
    seen: set[str] = set()
    for group in GROUP_ORDER:
        group_entries = load_watchlist_entries(group=group)
        group_entries.sort(key=lambda item: (item.priority if item.priority is not None else 99, item.code))
        for entry in group_entries:
            if entry.code not in seen:
                seen.add(entry.code)
                entries.append(entry)
    return entries


def run_daily_job(
    job_id: str,
    sleep_seconds: float = 1.5,
    fetcher: Callable[..., Any] = fetch_longbridge_stock,
    path: Path = DB_PATH,
) -> dict[str, Any]:
    """Fetch the three watchlist groups sequentially and record every outcome."""

    _set_job(job_id, "running", {"started": now_iso()}, path)
    rows: list[dict[str, Any]] = []
    entries = _daily_entries()
    for index, entry in enumerate(entries):
        if index:
            time.sleep(max(0.0, sleep_seconds))
        try:
            data = fetcher(entry.code, timeout=30.0, path=path)
            rows.append({
                "code": entry.code,
                "group": ";".join(entry.groups),
                "ok": True,
                "data_date": data.data_date,
                "participant_count": len(data.holdings),
                "warnings": list(data.warnings),
            })
        except LongbridgeAuthError as exc:
            rows.append({"code": entry.code, "group": ";".join(entry.groups), "ok": False,
                         "error_type": "LONGBRIDGE_AUTH_EXPIRED", "error": str(exc)})
            break
        except (LongbridgeError, Exception) as exc:  # individual stock failure is non-fatal
            rows.append({"code": entry.code, "group": ";".join(entry.groups), "ok": False,
                         "error_type": type(exc).__name__, "error": str(exc)})
    detail = {
        "total": len(rows),
        "succeeded": sum(1 for row in rows if row.get("ok")),
        "failed": sum(1 for row in rows if not row.get("ok")),
        "rate_limit_429": sum(1 for row in rows if row.get("error_type") == "HTTP_429"),
        "results": rows,
    }
    successful_dates = [str(row.get("data_date")) for row in rows if row.get("ok") and row.get("data_date")]
    try:
        brief_date = max(successful_dates) if successful_dates else hkt_today()
        detail["brief"] = build_brief(
            brief_date,
            path,
            fetch_stats={"fetched_ok": detail["succeeded"], "fetched_fail": detail["failed"]},
        )
    except Exception as exc:
        detail["brief_error"] = f"{type(exc).__name__}: {exc}"
    _set_job(job_id, "succeeded" if detail["failed"] == 0 else "failed", detail, path)
    return get_job(job_id, path) or {"job_id": job_id, **detail}


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


def build_timeline(code: str, from_date: str = "", to_date: str = "", path: Path = DB_PATH) -> list[dict[str, Any]]:
    rows = _holdings_rows(code, from_date, to_date, path)
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
) -> dict[str, Any]:
    timeline_codes = {entry.code for group in GROUP_ORDER for entry in load_watchlist_entries(group=group)}
    s1: list[dict[str, Any]] = []
    s2: list[dict[str, Any]] = []
    s3: list[dict[str, Any]] = []
    s4_watchlist: list[dict[str, Any]] = []
    s4_market: list[dict[str, Any]] = []
    for code in sorted(timeline_codes):
        rows = _holdings_rows(code, to_date=brief_date, path=path)
        issued = _issued_shares(code, path)
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
            if issued and abs(float(change or 0)) and pct not in (None, ""):
                if abs(float(change)) / issued >= 0.01:
                    s1.append({"code": code, "ccass_id": row["ccass_id"], "name": row["participant_name"],
                               "change_shares": change, "pct": round(abs(float(change)) / issued * 100, 6),
                               "holding_after": row["holding_shares"]})
        current_timeline = build_timeline(code, to_date=brief_date, path=path)
        for before, after in zip(current_timeline, current_timeline[1:]):
            before_top5 = before.get("top5_pct_ccass")
            after_top5 = after.get("top5_pct_ccass")
            if before_top5 is not None and after_top5 is not None and abs(after_top5 - before_top5) >= 2:
                s3.append({"code": code, "date": after["date"], "top5_before": before_top5, "top5_after": after_top5})
    # Quote rows are populated by a permitted market-data collector. Keep the
    # computation separate from holdings so missing quote coverage is visible.
    quote_rows = _query(
        "SELECT code,trade_date,turnover,market_cap FROM quote_daily WHERE trade_date<=? ORDER BY trade_date DESC",
        (brief_date,), path,
    )
    for quote in quote_rows:
        turnover = _as_float(quote.get("turnover"))
        if turnover is None or turnover < 3_500_000:
            continue
        history = _query(
            "SELECT turnover FROM quote_daily WHERE code=? AND trade_date<? AND turnover IS NOT NULL ORDER BY trade_date DESC LIMIT 20",
            (quote["code"], quote["trade_date"]), path,
        )
        median = _median([_as_float(row.get("turnover")) for row in history])
        if median and turnover / median >= 3:
            item = {"code": quote["code"], "trade_date": quote["trade_date"], "turnover": turnover,
                    "ratio": round(turnover / median, 6), "turnover_to_mcap": _ratio(turnover, _as_float(quote.get("market_cap")))}
            (s4_watchlist if quote["code"] in timeline_codes else s4_market).append(item)
    s5 = _event_signals(brief_date, timeline_codes)
    hypotheses = resolve_hypotheses(brief_date, path)
    trade_date_covered, trade_date_warning = shift_trading_date(brief_date, -2)
    data_quality = []
    if trade_date_warning:
        data_quality.append(trade_date_warning)
    payload = {
        "brief_date": brief_date,
        "data_date": brief_date,
        "trade_date_covered": trade_date_covered,
        "coverage": {
            **{group: len(load_watchlist_entries(group=group)) for group in GROUP_ORDER},
            **(fetch_stats or {}),
        },
        "signals": {"S1": s1, "S2": s2, "S3": s3, "S4_watchlist": s4_watchlist, "S4_market": s4_market, "S5": s5},
        "hypotheses_resolved": hypotheses,
        "hypotheses_pending_next3d": [item for item in get_hypotheses(status="pending", path=path)
                                      if str(item["expected_date"]) <= (date.fromisoformat(brief_date) + timedelta(days=3)).isoformat()],
        "data_quality": data_quality,
    }
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
