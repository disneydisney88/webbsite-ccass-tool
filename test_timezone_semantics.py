import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

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


def test_server_hkt_clock_is_explicit_under_host_timezone_variants():
    root = Path(__file__).resolve().parent
    code = "from api import server_time_values; print(server_time_values()[1].isoformat())"
    for host_tz in ("UTC", "Europe/London"):
        env = os.environ.copy()
        env["TZ"] = host_tz
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        assert "+08:00" in completed.stdout.strip()
