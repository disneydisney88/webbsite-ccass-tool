from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .fetcher import FetchResult, html_to_text, now_iso
from .parse_sdw import SDWSnapshot


DATA_DIR = Path(os.getenv("CCASS_DATA_DIR", "data"))
DB_PATH = Path(os.getenv("CCASS_SNAPSHOT_DB", str(DATA_DIR / "ccass_snapshots.db")))
WATCHLIST_PATH = Path(os.getenv("CCASS_WATCHLIST", str(DATA_DIR / "watchlist.csv")))
BACKUP_LATEST_PATH = Path(os.getenv("CCASS_SNAPSHOT_BACKUP_LATEST", str(DATA_DIR / "backups" / "ccass_snapshots_latest.db")))
_DB_RESTORED_FROM_BACKUP = False
_DB_RESTORE_SOURCE = ""


@dataclass
class SnapshotBuildResult:
    results: dict[str, FetchResult]
    stock_name: str = ""
    issued_shares: str = ""
    latest_date: str = ""
    previous_date: str = ""
    history_depth_days: int = 0
    warnings: list[str] | None = None


@dataclass(frozen=True)
class WatchlistEntry:
    code: str
    name: str = ""
    groups: tuple[str, ...] = ()


def restore_snapshot_db_from_backup(path: Path = DB_PATH, backup_path: Path = BACKUP_LATEST_PATH) -> bool:
    global _DB_RESTORED_FROM_BACKUP, _DB_RESTORE_SOURCE
    if Path(path) != DB_PATH and Path(backup_path) == BACKUP_LATEST_PATH:
        return False
    if path.exists() or not backup_path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, path)
    _DB_RESTORED_FROM_BACKUP = True
    try:
        dated = datetime.fromtimestamp(backup_path.stat().st_mtime, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except OSError:
        dated = "unknown"
    _DB_RESTORE_SOURCE = f"{backup_path} dated {dated}"
    return True


def db_restore_status() -> dict[str, Any]:
    return {"db_restored_from_backup": _DB_RESTORED_FROM_BACKUP, "db_restore_source": _DB_RESTORE_SOURCE}


def parse_groups(value: str) -> tuple[str, ...]:
    groups: list[str] = []
    for item in (value or "").replace("|", ";").split(";"):
        group = item.strip().lower()
        if group and group not in groups:
            groups.append(group)
    return tuple(groups)


def entry_matches_group(entry: WatchlistEntry, group: str | None) -> bool:
    wanted = (group or "").strip().lower()
    return not wanted or wanted in entry.groups


def ensure_db(path: Path = DB_PATH) -> None:
    restore_snapshot_db_from_backup(path=path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                participant_name TEXT NOT NULL,
                shares INTEGER NOT NULL,
                pct_of_issued REAL,
                source TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (code, date, participant_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_meta (
                code TEXT PRIMARY KEY,
                name TEXT,
                issued_shares TEXT,
                issued_shares_as_of TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_map (
                code TEXT PRIMARY KEY,
                issue_id TEXT NOT NULL,
                name TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                close REAL,
                open REAL,
                high REAL,
                low REAL,
                volume INTEGER,
                turnover REAL,
                vwap REAL,
                price_source TEXT NOT NULL,
                turnover_est REAL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (code, date, price_source)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mirror_probe (
                probe_date TEXT NOT NULL,
                mirror_base_url TEXT NOT NULL,
                status TEXT NOT NULL,
                browser_sections TEXT NOT NULL,
                error_message TEXT NOT NULL DEFAULT '',
                probed_at TEXT NOT NULL,
                PRIMARY KEY (probe_date, mirror_base_url)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS longbridge_holdings_daily (
                code TEXT NOT NULL,
                data_date TEXT NOT NULL,
                ccass_id TEXT NOT NULL,
                participant_name TEXT NOT NULL,
                holding_shares INTEGER NOT NULL,
                stake_pct_of_issued REAL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (code, data_date, ccass_id)
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(longbridge_holdings_daily)")}
        if "change_shares" not in columns:
            conn.execute("ALTER TABLE longbridge_holdings_daily ADD COLUMN change_shares REAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS longbridge_credentials (
                credential_id TEXT PRIMARY KEY,
                encrypted_payload BLOB NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def upsert_longbridge_holdings(
    code: str,
    data_date: str,
    rows: list[dict[str, Any]],
    path: Path = DB_PATH,
) -> int:
    ensure_db(path)
    fetched_at = now_iso()
    values = []
    for row in rows:
        ccass_id = str(row.get("ccass_id") or "").strip().upper()
        if not ccass_id:
            continue
        values.append(
            (
                str(code).zfill(5),
                str(data_date),
                ccass_id,
                str(row.get("participant_name") or ""),
                int(row.get("holding_shares") or 0),
                row.get("stake_pct_of_issued"),
                row.get("change_shares"),
                fetched_at,
            )
        )
    with closing(sqlite3.connect(path)) as conn:
        conn.executemany(
            """
            INSERT INTO longbridge_holdings_daily
                (code, data_date, ccass_id, participant_name, holding_shares,
                 stake_pct_of_issued, change_shares, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code, data_date, ccass_id) DO UPDATE SET
                participant_name=excluded.participant_name,
                holding_shares=excluded.holding_shares,
                stake_pct_of_issued=excluded.stake_pct_of_issued,
                change_shares=excluded.change_shares,
                fetched_at=excluded.fetched_at
            """,
            values,
        )
        conn.commit()
    return len(values)


def load_longbridge_holdings(
    code: str,
    data_date: str = "",
    path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    ensure_db(path)
    normalized = str(code).zfill(5)
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        wanted_date = data_date
        if not wanted_date:
            row = conn.execute(
                "SELECT MAX(data_date) AS data_date FROM longbridge_holdings_daily WHERE code=?",
                (normalized,),
            ).fetchone()
            wanted_date = str(row["data_date"] or "") if row else ""
        rows = conn.execute(
            """
            SELECT code, data_date, ccass_id, participant_name, holding_shares,
                   stake_pct_of_issued, change_shares, fetched_at
            FROM longbridge_holdings_daily
            WHERE code=? AND data_date=?
            ORDER BY holding_shares DESC, ccass_id
            """,
            (normalized, wanted_date),
        ).fetchall()
    return [dict(row) for row in rows]


def load_longbridge_holding_history(
    code: str,
    path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    """Load persisted Longbridge participant snapshots for history charts."""
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT code, data_date, ccass_id, participant_name, holding_shares,
                   stake_pct_of_issued, change_shares, fetched_at
            FROM longbridge_holdings_daily
            WHERE code=?
            ORDER BY data_date ASC, holding_shares DESC, ccass_id
            """,
            (str(code).zfill(5),),
        ).fetchall()
    return [dict(row) for row in rows]


def load_longbridge_snapshot_dates(
    code: str,
    limit: int = 2,
    path: Path = DB_PATH,
) -> list[str]:
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT data_date FROM longbridge_holdings_daily
            WHERE code=? ORDER BY data_date DESC LIMIT ?
            """,
            (str(code).zfill(5), max(1, int(limit))),
        ).fetchall()
    return [str(row[0]) for row in rows]


def save_longbridge_secret(
    credential_id: str,
    encrypted_payload: bytes,
    path: Path = DB_PATH,
) -> None:
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            INSERT INTO longbridge_credentials (credential_id, encrypted_payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(credential_id) DO UPDATE SET
                encrypted_payload=excluded.encrypted_payload,
                updated_at=excluded.updated_at
            """,
            (credential_id, encrypted_payload, now_iso()),
        )
        conn.commit()


def load_longbridge_secret(credential_id: str, path: Path = DB_PATH) -> bytes | None:
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute(
            "SELECT encrypted_payload FROM longbridge_credentials WHERE credential_id=?",
            (credential_id,),
        ).fetchone()
    return bytes(row[0]) if row else None


def delete_longbridge_secret(credential_id: str, path: Path = DB_PATH) -> None:
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DELETE FROM longbridge_credentials WHERE credential_id=?", (credential_id,))
        conn.commit()


def save_longbridge_credential(encrypted_payload: bytes, path: Path = DB_PATH) -> None:
    save_longbridge_secret("primary", encrypted_payload, path=path)


def load_longbridge_credential(path: Path = DB_PATH) -> bytes | None:
    return load_longbridge_secret("primary", path=path)


def snapshot_exists(code: str, date: str, path: Path = DB_PATH) -> bool:
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute("SELECT 1 FROM snapshots WHERE code=? AND date=? LIMIT 1", (code, date)).fetchone()
    return row is not None


def upsert_snapshot(snapshot: SDWSnapshot, source: str = "sdw", path: Path = DB_PATH) -> None:
    ensure_db(path)
    fetched_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    rows = snapshot.rows or []
    with closing(sqlite3.connect(path)) as conn:
        conn.executemany(
            """
            INSERT INTO snapshots
                (code, date, participant_id, participant_name, shares, pct_of_issued, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code, date, participant_id) DO UPDATE SET
                participant_name=excluded.participant_name,
                shares=excluded.shares,
                pct_of_issued=excluded.pct_of_issued,
                source=excluded.source,
                fetched_at=excluded.fetched_at
            """,
            [
                (
                    snapshot.code,
                    snapshot.date,
                    row["participant_id"],
                    row["participant_name"],
                    int(row.get("shares") or 0),
                    row.get("pct_of_issued"),
                    source,
                    fetched_at,
                )
                for row in rows
            ],
        )
        conn.execute(
            """
            INSERT INTO stock_meta (code, name, issued_shares, issued_shares_as_of)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name=COALESCE(NULLIF(excluded.name, ''), stock_meta.name),
                issued_shares=COALESCE(NULLIF(excluded.issued_shares, ''), stock_meta.issued_shares),
                issued_shares_as_of=excluded.issued_shares_as_of
            """,
            (snapshot.code, snapshot.stock_name, snapshot.issued_shares, snapshot.date),
        )
        conn.commit()


def latest_snapshot_date(code: str, path: Path = DB_PATH) -> str:
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute("SELECT MAX(date) FROM snapshots WHERE code=?", (code,)).fetchone()
    return row[0] or ""


def previous_snapshot_date(code: str, date: str, path: Path = DB_PATH) -> str:
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute("SELECT MAX(date) FROM snapshots WHERE code=? AND date < ?", (code, date)).fetchone()
    return row[0] or ""


def history_depth_days(code: str, path: Path = DB_PATH) -> int:
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute("SELECT COUNT(DISTINCT date) FROM snapshots WHERE code=?", (code,)).fetchone()
    return int(row[0] or 0)


def stock_fetched_today(code: str, path: Path = DB_PATH) -> bool:
    ensure_db(path)
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute(
            "SELECT 1 FROM snapshots WHERE code=? AND substr(fetched_at, 1, 10)=? LIMIT 1",
            (code, today),
        ).fetchone()
    return row is not None


def load_snapshot(code: str, date: str, path: Path = DB_PATH) -> pd.DataFrame:
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        return pd.read_sql_query(
            """
            SELECT participant_id, participant_name, shares, pct_of_issued, source, fetched_at
            FROM snapshots
            WHERE code=? AND date=?
            ORDER BY shares DESC
            """,
            conn,
            params=(code, date),
        )


def load_stock_meta(code: str, path: Path = DB_PATH) -> dict[str, Any]:
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute("SELECT name, issued_shares, issued_shares_as_of FROM stock_meta WHERE code=?", (code,)).fetchone()
    if not row:
        return {"name": "", "issued_shares": "", "issued_shares_as_of": ""}
    return {"name": row[0] or "", "issued_shares": row[1] or "", "issued_shares_as_of": row[2] or ""}


def upsert_stock_map(code: str, issue_id: str, name: str = "", path: Path = DB_PATH) -> None:
    ensure_db(path)
    updated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            INSERT INTO stock_map (code, issue_id, name, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                issue_id=excluded.issue_id,
                name=COALESCE(NULLIF(excluded.name, ''), stock_map.name),
                updated_at=excluded.updated_at
            """,
            (code, issue_id, name, updated_at),
        )
        conn.commit()


def load_stock_map(code: str, path: Path = DB_PATH) -> dict[str, Any]:
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute(
            "SELECT issue_id, name, updated_at FROM stock_map WHERE code=?",
            (code,),
        ).fetchone()
    if not row:
        return {"issue_id": "", "name": "", "updated_at": ""}
    return {"issue_id": row[0] or "", "name": row[1] or "", "updated_at": row[2] or ""}


def load_mirror_probe(probe_date: str, mirror_base_url: str, path: Path = DB_PATH) -> dict[str, Any] | None:
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute(
            "SELECT status, browser_sections, error_message, probed_at FROM mirror_probe WHERE probe_date=? AND mirror_base_url=?",
            (probe_date, mirror_base_url),
        ).fetchone()
    if not row:
        return None
    try:
        browser_sections = json.loads(row[1] or "[]")
    except (TypeError, ValueError):
        browser_sections = []
    return {
        "date": probe_date,
        "mirror_base_url": mirror_base_url,
        "status": row[0] or "unknown",
        "browser_sections": browser_sections if isinstance(browser_sections, list) else [],
        "error_message": row[2] or "",
        "probed_at": row[3] or "",
    }


def upsert_mirror_probe(
    probe_date: str,
    mirror_base_url: str,
    status: str,
    browser_sections: list[str] | None = None,
    error_message: str = "",
    path: Path = DB_PATH,
) -> None:
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            INSERT INTO mirror_probe (probe_date, mirror_base_url, status, browser_sections, error_message, probed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(probe_date, mirror_base_url) DO UPDATE SET
                status=excluded.status,
                browser_sections=excluded.browser_sections,
                error_message=excluded.error_message,
                probed_at=excluded.probed_at
            """,
            (
                probe_date,
                mirror_base_url,
                status,
                json.dumps(browser_sections or [], ensure_ascii=False),
                error_message,
                now_iso(),
            ),
        )
        conn.commit()


def upsert_price_history(code: str, table: pd.DataFrame, source: str = "yahoo", path: Path = DB_PATH) -> int:
    ensure_db(path)
    if table is None or table.empty:
        return 0
    fetched_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    def number(value: Any) -> float | None:
        try:
            if pd.isna(value) or value == "":
                return None
        except (TypeError, ValueError):
            pass
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    rows = []
    for _, row in table.iterrows():
        date = str(row.get("Date", "") or "").strip()
        if not date:
            continue
        rows.append(
            (
                code,
                date,
                number(row.get("Close")),
                number(row.get("Open")),
                number(row.get("High")),
                number(row.get("Low")),
                int(number(row.get("Volume")) or 0),
                number(row.get("Turnover")),
                number(row.get("VWAP")),
                str(row.get("price_source", source) or source),
                number(row.get("turnover_est")),
                fetched_at,
            )
        )
    with closing(sqlite3.connect(path)) as conn:
        conn.executemany(
            """
            INSERT INTO price_history
                (code, date, close, open, high, low, volume, turnover, vwap, price_source, turnover_est, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code, date, price_source) DO UPDATE SET
                close=excluded.close,
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                volume=excluded.volume,
                turnover=excluded.turnover,
                vwap=excluded.vwap,
                turnover_est=excluded.turnover_est,
                fetched_at=excluded.fetched_at
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def load_price_history(code: str, limit: int = 90, path: Path = DB_PATH) -> pd.DataFrame:
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        df = pd.read_sql_query(
            """
            SELECT date AS Date, close AS Close, open AS Open, high AS High, low AS Low,
                   volume AS Volume, turnover AS Turnover, vwap AS VWAP,
                   price_source, turnover_est
            FROM price_history
            WHERE code=?
            ORDER BY date DESC
            LIMIT ?
            """,
            conn,
            params=(code, int(limit)),
        )
    if df.empty:
        return df
    for column in ["Close", "Open", "High", "Low", "VWAP"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").round(3)
    for column in ["Turnover", "turnover_est"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").round(2)
    if "vwap_est" not in df.columns:
        volume = pd.to_numeric(df.get("Volume"), errors="coerce")
        turnover_est = pd.to_numeric(df.get("turnover_est"), errors="coerce")
        yahoo_source = df.get("price_source", "").astype(str).str.lower().eq("yahoo")
        missing_vwap = pd.to_numeric(df.get("VWAP"), errors="coerce").isna()
        df["vwap_est"] = (turnover_est / volume.where(volume.ne(0))).where(yahoo_source & missing_vwap).round(3)
    return df


def holdings_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Rank", "Participant", "CCASS ID", "Holding", "Stake %", "Cumulative %"])
    out = df.sort_values("shares", ascending=False).reset_index(drop=True)
    out["cumulative_pct"] = out["pct_of_issued"].fillna(0).cumsum()
    return pd.DataFrame(
        {
            "Rank": out.index + 1,
            "Participant": out["participant_name"],
            "CCASS ID": out["participant_id"],
            "Holding": out["shares"],
            "Stake %": out["pct_of_issued"].map(lambda value: "" if pd.isna(value) else f"{float(value):.2f}%"),
            "Cumulative %": out["cumulative_pct"].map(lambda value: f"{float(value):.2f}%"),
        }
    )


def concentration_table(code: str, path: Path = DB_PATH) -> pd.DataFrame:
    ensure_db(path)
    with closing(sqlite3.connect(path)) as conn:
        dates = [row[0] for row in conn.execute("SELECT DISTINCT date FROM snapshots WHERE code=? ORDER BY date DESC", (code,))]
    records = []
    for date in dates:
        df = load_snapshot(code, date, path)
        total_pct = float(df["pct_of_issued"].fillna(0).sum()) if not df.empty else 0.0
        top5 = float(df.head(5)["pct_of_issued"].fillna(0).sum()) if not df.empty else 0.0
        top10 = float(df.head(10)["pct_of_issued"].fillna(0).sum()) if not df.empty else 0.0
        records.append(
            {
                "Date": date,
                "Top 5 %": f"{top5:.2f}%",
                "Top 10 %": f"{top10:.2f}%",
                "Top 10 + NCIP %": "",
                "Stake in CCASS %": f"{total_pct:.2f}%",
                "Top 5 % of CCASS": f"{(top5 / total_pct * 100):.2f}%" if total_pct else "",
                "Top 10 % of CCASS": f"{(top10 / total_pct * 100):.2f}%" if total_pct else "",
            }
        )
    return pd.DataFrame(records)


def changes_table(current: pd.DataFrame, previous: pd.DataFrame) -> pd.DataFrame:
    columns = ["Participant", "Change", "Change %", "Holding after", "Stake after"]
    if current.empty or previous.empty:
        return pd.DataFrame(columns=columns)
    cur = current.set_index("participant_id")
    prev = previous.set_index("participant_id")
    ids = sorted(set(cur.index) | set(prev.index))
    rows = []
    for participant_id in ids:
        cur_row = cur.loc[participant_id] if participant_id in cur.index else None
        prev_row = prev.loc[participant_id] if participant_id in prev.index else None
        cur_shares = int(cur_row["shares"]) if cur_row is not None else 0
        prev_shares = int(prev_row["shares"]) if prev_row is not None else 0
        change = cur_shares - prev_shares
        if change == 0:
            continue
        cur_pct = cur_row["pct_of_issued"] if cur_row is not None else None
        prev_pct = prev_row["pct_of_issued"] if prev_row is not None else None
        pct_change = ""
        if cur_pct is not None and prev_pct is not None and not pd.isna(cur_pct) and not pd.isna(prev_pct):
            pct_change = f"{float(cur_pct) - float(prev_pct):+.2f}%"
        name = str(cur_row["participant_name"] if cur_row is not None else prev_row["participant_name"])
        rows.append(
            {
                "Participant": name,
                "Change": change,
                "Change %": pct_change,
                "Holding after": cur_shares,
                "Stake after": "" if cur_pct is None or pd.isna(cur_pct) else f"{float(cur_pct):.2f}%",
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values("Change", key=lambda s: s.abs(), ascending=False)


def _fetch_result(name: str, url: str, table: pd.DataFrame, raw_text: str, ok: bool = True, error_type: str = "", error_message: str = "") -> FetchResult:
    return FetchResult(
        name=name,
        url=url,
        final_url=url,
        status=200 if ok else None,
        fetched_time=now_iso(),
        raw_text=raw_text,
        tables=[] if table.empty else [table],
        method="sdw+local_db",
        ok=ok,
        error_type=error_type,
        error_message=error_message,
    )


def build_results_from_db(code: str, source_url: str, path: Path = DB_PATH) -> SnapshotBuildResult:
    latest = latest_snapshot_date(code, path)
    if not latest:
        warning = "LOCAL_SNAPSHOT_EMPTY: no SDW snapshot is available for this stock."
        return SnapshotBuildResult(results={}, warnings=[warning])
    previous = previous_snapshot_date(code, latest, path)
    current_df = load_snapshot(code, latest, path)
    previous_df = load_snapshot(code, previous, path) if previous else pd.DataFrame()
    meta = load_stock_meta(code, path)

    holdings = holdings_table(current_df)
    concentration = concentration_table(code, path)
    changes = changes_table(current_df, previous_df)
    total_pct = current_df["pct_of_issued"].fillna(0).sum() if not current_df.empty else 0
    total_shares = current_df["shares"].sum() if not current_df.empty else 0
    raw = (
        f"SDW local snapshot\nStock code: {code}\nName: {meta.get('name', '')}\n"
        f"CCASS holdings on {latest}\nIssued securities: {meta.get('issued_shares', '')}\n"
        f"Total securities in CCASS: {total_shares}\nStake in CCASS: {total_pct:.2f}%\n"
    )
    results = {
        "Company / orgdata": _fetch_result(
            "Company / orgdata",
            f"local_db://stock_meta/{code}",
            pd.DataFrame([{"Code": code, "Name": meta.get("name", ""), "Source": "HKEX SDW local snapshot"}]),
            raw,
        ),
        "Holdings": _fetch_result("Holdings", source_url, holdings, raw),
        "Concentration": _fetch_result("Concentration", "local_db://ccass_concentration", concentration, raw),
    }
    if previous and not changes.empty:
        change_raw = f"Trading date: {latest}\nDate range: {previous} to {latest}\nTotal CCASS change: {int(current_df['shares'].sum() - previous_df['shares'].sum())}"
        results["Changes"] = _fetch_result("Changes", "local_db://ccass_changes", changes, change_raw)
    else:
        message = f"History limited to local snapshots since {latest}; mirror historical data unavailable"
        results["Changes"] = _fetch_result("Changes", "local_db://ccass_changes", pd.DataFrame(), raw, False, "LOCAL_HISTORY_LIMITED", message)
    results["Big Changes"] = _fetch_result(
        "Big Changes",
        "local_db://ccass_big_changes",
        pd.DataFrame(),
        raw,
        False,
        "LOCAL_HISTORY_LIMITED",
        "Big Changes require local history or mirror historical data; not enough SDW snapshots are available yet.",
    )
    price_table = load_price_history(code, path=path)
    if price_table.empty:
        results["Price History"] = _fetch_result(
            "Price History",
            "local_db://price_history",
            pd.DataFrame(),
            raw,
            False,
            "SOURCE_UNAVAILABLE",
            "Price History is only available from the mirror or Yahoo fallback source.",
        )
    else:
        results["Price History"] = _fetch_result("Price History", "local_db://price_history", price_table, raw)
    return SnapshotBuildResult(
        results=results,
        stock_name=meta.get("name", ""),
        issued_shares=meta.get("issued_shares", ""),
        latest_date=latest,
        previous_date=previous,
        history_depth_days=history_depth_days(code, path),
        warnings=[],
    )


def export_db_bytes(path: Path = DB_PATH) -> bytes:
    ensure_db(path)
    return path.read_bytes()


def load_watchlist_entries(path: Path = WATCHLIST_PATH, group: str | None = None) -> list[WatchlistEntry]:
    if not path.exists():
        return []
    if path.suffix.lower() == ".json":
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        raw_items = data if isinstance(data, list) else data.get("codes", [])
        entries = []
        for item in raw_items:
            if isinstance(item, dict):
                entry = WatchlistEntry(
                    code=str(item.get("code", "")).zfill(5),
                    name=str(item.get("name", "") or ""),
                    groups=parse_groups(str(item.get("group", "") or item.get("groups", ""))),
                )
            else:
                entry = WatchlistEntry(code=str(item).zfill(5))
            if entry.code.strip("0") and entry_matches_group(entry, group):
                entries.append(entry)
        return entries

    entries: list[WatchlistEntry] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames and "code" in [field.strip().lower() for field in reader.fieldnames]:
            for row in reader:
                normalized = {str(key).strip().lower(): value for key, value in row.items()}
                code = str(normalized.get("code", "") or "").strip().zfill(5)
                entry = WatchlistEntry(
                    code=code,
                    name=str(normalized.get("name", "") or "").strip(),
                    groups=parse_groups(str(normalized.get("group", "") or "")),
                )
                if code.strip("0") and entry_matches_group(entry, group):
                    entries.append(entry)
        else:
            handle.seek(0)
            for row in csv.reader(handle):
                if row and row[0].strip() and not row[0].strip().lower().startswith("code"):
                    entry = WatchlistEntry(code=row[0].strip().zfill(5))
                    if entry_matches_group(entry, group):
                        entries.append(entry)
    deduped: dict[str, WatchlistEntry] = {}
    for entry in entries:
        deduped.setdefault(entry.code, entry)
    return list(deduped.values())


def load_watchlist(path: Path = WATCHLIST_PATH, group: str | None = None) -> list[str]:
    return [entry.code for entry in load_watchlist_entries(path=path, group=group)]
