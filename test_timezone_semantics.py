from datetime import datetime

from utils.date_semantics import next_trading_date
from utils.fetcher import hkt_today, now_iso


def test_business_date_and_timestamp_are_timezone_explicit():
    value = hkt_today()
    assert datetime.strptime(value, "%Y-%m-%d").date().isoformat() == value
    timestamp = now_iso()
    assert timestamp.endswith("Z")
    assert datetime.fromisoformat(timestamp.replace("Z", "+00:00")).tzinfo is not None


def test_next_trading_date_returns_a_later_iso_session():
    next_date, warning = next_trading_date("2026-09-06")
    assert next_date >= "2026-09-07"
    assert next_date.count("-") == 2
    assert isinstance(warning, str)
