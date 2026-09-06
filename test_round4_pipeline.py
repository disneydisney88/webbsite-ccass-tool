from pathlib import Path
import sqlite3

from utils.longbridge import LongbridgeData
from utils.research_pipeline import (
    build_brief,
    build_timeline,
    build_transfer_candidates,
    get_job,
    run_daily_job,
    start_job,
)
from utils.snapshot_db import ensure_db, upsert_longbridge_holdings


def test_daily_job_is_idempotent_and_writes_unified_rows(tmp_path, monkeypatch):
    db = tmp_path / "round4.db"
    monkeypatch.setattr("utils.research_pipeline.DB_PATH", db)
    monkeypatch.setattr("utils.snapshot_db.DB_PATH", db)
    ensure_db(db)

    class Entry:
        code = "06182"
        groups = ("lshape79",)
        priority = 1

    monkeypatch.setattr("utils.research_pipeline.load_watchlist_entries", lambda group=None: [Entry()])

    def fake_fetch(code, timeout, path):
        upsert_longbridge_holdings(code, "2026-09-01", [{
            "ccass_id": "B01438", "participant_name": "KGI", "holding_shares": 540_900_000,
            "stake_pct_of_issued": 67.61, "change_shares": -90_000_000,
        }], path=path)
        return LongbridgeData(code=code, data_date="2026-09-01", holdings=[{"ccass_id": "B01438"}])

    job, created = start_job("daily:2026-09-01", path=db)
    assert created is True
    result = run_daily_job("daily:2026-09-01", sleep_seconds=0, fetcher=fake_fetch, path=db)
    assert result["status"] == "succeeded"
    assert get_job("daily:2026-09-01", db)["status"] == "succeeded"
    _, created_again = start_job("daily:2026-09-01", path=db)
    assert created_again is False


def test_timeline_and_transfer_candidate_shape(tmp_path, monkeypatch):
    db = tmp_path / "timeline.db"
    monkeypatch.setattr("utils.research_pipeline.DB_PATH", db)
    monkeypatch.setattr("utils.snapshot_db.DB_PATH", db)
    ensure_db(db)
    upsert_longbridge_holdings("06182", "2026-08-31", [
        {"ccass_id": "B01438", "participant_name": "KGI", "holding_shares": 540_900_000, "stake_pct_of_issued": 67.61, "change_shares": -90_000_000},
        {"ccass_id": "B02094", "participant_name": "Other", "holding_shares": 90_000_000, "stake_pct_of_issued": 11.26, "change_shares": 90_000_000},
    ], path=db)
    # The candidate is allowed only when issued shares and market volume are known.
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS quote_daily (code TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL, turnover REAL, volume INTEGER, market_cap REAL, fetched_at TEXT NOT NULL, PRIMARY KEY (code, trade_date))")
        conn.execute("INSERT INTO stock_meta(code, name, issued_shares, issued_shares_as_of) VALUES ('06182','Test','800000000','2026-08-31')")
        conn.execute("INSERT INTO quote_daily(code, trade_date, volume, fetched_at) VALUES ('06182','2026-08-31',1000000,'2026-09-01T00:00:00+00:00')")
        conn.commit()
    class Entry:
        code = "06182"
        groups = ("lshape79",)
    monkeypatch.setattr("utils.research_pipeline.load_watchlist_entries", lambda group=None: [Entry()])
    timeline = build_timeline("06182", path=db)
    assert timeline[0]["top1_id"] == "B01438"
    transfers = build_transfer_candidates("lshape79", path=db)
    assert transfers and transfers[0]["from_id"] == "B01438"


def test_brief_emits_first_seen_signal_and_coverage_dates(tmp_path, monkeypatch):
    db = tmp_path / "brief.db"
    monkeypatch.setattr("utils.research_pipeline.DB_PATH", db)
    monkeypatch.setattr("utils.snapshot_db.DB_PATH", db)
    ensure_db(db)

    upsert_longbridge_holdings("06182", "2026-09-01", [
        {"ccass_id": "B01438", "participant_name": "KGI", "holding_shares": 60_000_000,
         "stake_pct_of_issued": 7.5, "change_shares": 60_000_000},
    ], path=db)
    upsert_longbridge_holdings("06182", "2026-09-02", [
        {"ccass_id": "B01438", "participant_name": "KGI", "holding_shares": 60_000_000,
         "stake_pct_of_issued": 7.5, "change_shares": 0},
    ], path=db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO stock_meta(code, name, issued_shares, issued_shares_as_of) VALUES (?, ?, ?, ?)",
                     ("06182", "Test", "800000000", "2026-09-02"))
        conn.commit()

    class Entry:
        code = "06182"
        groups = ("lshape79",)

    monkeypatch.setattr("utils.research_pipeline.load_watchlist_entries", lambda group=None: [Entry()])
    brief = build_brief("2026-09-02", path=db, fetch_stats={"fetched_ok": 1, "fetched_fail": 0})

    assert brief["trade_date_covered"] == "2026-08-31"
    assert brief["coverage"]["fetched_ok"] == 1
    assert brief["coverage"]["fetched_fail"] == 0
    assert brief["signals"]["S2"] == [{
        "code": "06182", "ccass_id": "B01438", "name": "KGI",
        "change_shares": 60_000_000, "pct": 7.5,
        "holding_after": 60_000_000, "first_seen": "2026-09-01",
    }]


def test_daily_job_filters_groups_and_persists_progress(tmp_path, monkeypatch):
    db = tmp_path / "groups.db"
    monkeypatch.setattr("utils.research_pipeline.DB_PATH", db)
    monkeypatch.setattr("utils.snapshot_db.DB_PATH", db)
    ensure_db(db)

    class Entry:
        def __init__(self, code, groups):
            self.code = code
            self.groups = groups
            self.priority = 1

    entries = {
        "lshape79": [Entry("00001", ("lshape79",))],
        "caiji": [Entry("00002", ("caiji",))],
        "research": [Entry("00003", ("research",))],
    }
    monkeypatch.setattr(
        "utils.research_pipeline.load_watchlist_entries",
        lambda group=None: list(entries.get(group, [])),
    )

    def fake_fetch(code, timeout, path):
        return LongbridgeData(code=code, data_date="2026-09-07", holdings=[{"ccass_id": "B00001"}])

    start_job("daily:2026-09-07", path=db)
    result = run_daily_job(
        "daily:2026-09-07", sleep_seconds=0, fetcher=fake_fetch,
        groups=("lshape79", "caiji"), path=db,
    )
    detail = result["detail"]
    assert [row["code"] for row in detail["results"]] == ["00001", "00002"]
    assert detail["total"] == 2
    assert detail["succeeded"] == 2
    assert detail["current_code"] == ""
    assert detail["elapsed_s"] >= 0


def test_daily_job_can_be_cancelled_between_stocks(tmp_path, monkeypatch):
    db = tmp_path / "cancel.db"
    monkeypatch.setattr("utils.research_pipeline.DB_PATH", db)
    monkeypatch.setattr("utils.snapshot_db.DB_PATH", db)
    ensure_db(db)

    class Entry:
        def __init__(self, code):
            self.code = code
            self.groups = ("lshape79",)
            self.priority = 1

    monkeypatch.setattr(
        "utils.research_pipeline.load_watchlist_entries",
        lambda group=None: [Entry("00001"), Entry("00002")],
    )
    calls = []

    def fake_fetch(code, timeout, path):
        calls.append(code)
        return LongbridgeData(code=code, data_date="2026-09-07", holdings=[])

    start_job("daily:cancel", path=db)
    result = run_daily_job(
        "daily:cancel", sleep_seconds=0, fetcher=fake_fetch,
        groups=("lshape79",), cancel_requested=lambda: bool(calls), path=db,
    )
    assert result["status"] == "cancelled"
    assert calls == ["00001"]
    assert result["detail"]["current_code"] == ""
