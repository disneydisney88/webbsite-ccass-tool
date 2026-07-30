from __future__ import annotations

import csv
import os
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


@dataclass
class SnapshotBuildResult:
    results: dict[str, FetchResult]
    stock_name: str = ""
    issued_shares: str = ""
    latest_date: str = ""
    previous_date: str = ""
    history_depth_days: int = 0
    warnings: list[str] | None = None


def ensure_db(path: Path = DB_PATH) -> None:
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
        conn.commit()


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
    results["Price History"] = _fetch_result(
        "Price History",
        "local_db://price_history",
        pd.DataFrame(),
        raw,
        False,
        "SOURCE_UNAVAILABLE",
        "Price History is only available from the mirror source.",
    )
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


def load_watchlist(path: Path = WATCHLIST_PATH) -> list[str]:
    if not path.exists():
        return []
    if path.suffix.lower() == ".json":
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(item).zfill(5) for item in data]
        return [str(item).zfill(5) for item in data.get("codes", [])]
    codes: list[str] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.reader(handle):
            if row and row[0].strip() and not row[0].strip().lower().startswith("code"):
                codes.append(row[0].strip().zfill(5))
    return codes
