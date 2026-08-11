from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
import re
from typing import Any


CALENDAR_NAME = "XHKG"
CALENDAR_SUPPORTED_FROM = date(1990, 1, 1)
CALENDAR_SUPPORTED_TO = date(2035, 12, 31)
FALLBACK_HOLIDAYS_PATH = Path(__file__).resolve().parents[1] / "data" / "hkex_holidays_fallback.csv"
CALENDAR_SOURCE_URL = (
    "https://www.hkex.com.hk/Services/Trading/Derivatives/Overview/"
    "Trading-Calendar-and-Holiday-Schedule?sc_lang=en"
)

# Changes is the one intentionally different section. The source page heading
# gives the CCASS settlement range, while its explicit "Trading date" field is
# the date represented by the participant-change rows.
SECTION_DATE_BASIS = {
    "Holdings": "settlement",
    "Changes": "trade",
    "Big Changes": "settlement",
    "Concentration": "settlement",
    "Price History": "trade",
}

SETTLEMENT_NOTE = (
    "CCASS uses T+2 settlement. Holdings, Big Changes and Concentration dates are holding/settlement "
    "dates. Changes rows use the page's explicit Trading date; the page's date "
    "range and d= query parameter are settlement dates. Implied dates use two "
    "XHKG trading sessions, including Hong Kong market holidays."
)

DATE_SEMANTICS_SUMMARY = (
    "日期基準：Holdings/Big Changes/Concentration = 持股日（結算日，T+2）；"
    "Changes = 頁面明列交易日。成交日與結算日以港股交易日曆相隔兩個交易日換算。"
)


@dataclass(frozen=True)
class DateDerivation:
    ccass_date: str = ""
    implied_trade_date: str = ""
    implied_settlement_date: str = ""
    date_basis: str = "unknown"
    warning: str = ""


def normalize_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return ""

    match = re.fullmatch(r"(\d{2})-(\d{2})-(\d{2})", text)
    if match:
        year, month, day = map(int, match.groups())
        try:
            return date(2000 + year, month, day).isoformat()
        except ValueError:
            return ""

    normalized = text.replace("/", "-")
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", normalized)
    if match:
        year, month, day = map(int, match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return ""
    return ""


def _coverage_warning(value: date) -> str:
    if CALENDAR_SUPPORTED_FROM <= value <= CALENDAR_SUPPORTED_TO:
        return ""
    return (
        f"HKEX trading calendar coverage is {CALENDAR_SUPPORTED_FROM.isoformat()} "
        f"to {CALENDAR_SUPPORTED_TO.isoformat()}; cannot safely derive a T+2 date "
        f"for {value.isoformat()}."
    )


@lru_cache(maxsize=1)
def _fallback_holidays() -> tuple[set[date], int, int]:
    holidays: set[date] = set()
    years: list[int] = []
    if not FALLBACK_HOLIDAYS_PATH.exists():
        return holidays, 0, 0
    with FALLBACK_HOLIDAYS_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            parsed = normalize_date(row.get("date"))
            if not parsed:
                continue
            value = date.fromisoformat(parsed)
            holidays.add(value)
            years.append(value.year)
    return holidays, min(years, default=0), max(years, default=0)


@lru_cache(maxsize=512)
def _sessions_between(start_iso: str, end_iso: str) -> tuple[tuple[date, ...], str]:
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    if start > end:
        start, end = end, start

    coverage_warning = _coverage_warning(start) or _coverage_warning(end)
    if coverage_warning:
        return (), coverage_warning

    try:
        import pandas_market_calendars as market_calendars

        calendar = market_calendars.get_calendar(CALENDAR_NAME)
        sessions = calendar.valid_days(start_date=start.isoformat(), end_date=end.isoformat())
        return tuple(item.date() for item in sessions), ""
    except (ImportError, ModuleNotFoundError):
        holidays, first_year, last_year = _fallback_holidays()
        if not first_year or start.year < first_year or end.year > last_year:
            return (), (
                "pandas_market_calendars is unavailable and the bundled HKEX holiday "
                f"fallback covers only {first_year or 'no'}-{last_year or 'no'}; "
                "implied dates were left blank."
            )
        sessions: list[date] = []
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5 and cursor not in holidays:
                sessions.append(cursor)
            cursor += timedelta(days=1)
        return tuple(sessions), (
            "pandas_market_calendars is unavailable; the bundled HKEX holiday fallback "
            f"({first_year}-{last_year}) was used."
        )
    except Exception as exc:
        return (), f"XHKG trading calendar failed: {type(exc).__name__}: {exc}"


def trading_sessions_between(start_value: Any, end_value: Any) -> tuple[list[str], str]:
    start_iso = normalize_date(start_value)
    end_iso = normalize_date(end_value)
    if not start_iso or not end_iso:
        return [], "Date range must use valid YYYY-MM-DD dates."
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    sessions, warning = _sessions_between(start.isoformat(), end.isoformat())
    return [item.isoformat() for item in sessions], warning


def shift_trading_date(value: Any, offset: int) -> tuple[str, str]:
    source_iso = normalize_date(value)
    if not source_iso:
        return "", "Source date is missing or invalid; implied date was left blank."
    source = date.fromisoformat(source_iso)
    coverage_warning = _coverage_warning(source)
    if coverage_warning:
        return "", coverage_warning
    if offset == 0:
        return source_iso, ""

    span = max(45, abs(offset) * 10)
    start = source - timedelta(days=span)
    end = source + timedelta(days=span)
    sessions, warning = _sessions_between(start.isoformat(), end.isoformat())
    if not sessions:
        return "", warning or f"No XHKG trading sessions were available around {source_iso}."
    if source not in sessions:
        return "", (
            f"{source_iso} is not an XHKG trading session; implied date was left blank."
        )
    target_index = sessions.index(source) + offset
    if target_index < 0 or target_index >= len(sessions):
        return "", f"XHKG calendar window was insufficient around {source_iso}."
    return sessions[target_index].isoformat(), warning


def derive_dates(value: Any, basis: str) -> DateDerivation:
    source_iso = normalize_date(value)
    normalized_basis = str(basis or "unknown").strip().lower()
    if normalized_basis not in {"settlement", "trade"}:
        return DateDerivation(
            ccass_date=source_iso,
            date_basis="unknown",
            warning="Date basis is unknown; implied trade and settlement dates were left blank.",
        )
    if not source_iso:
        return DateDerivation(
            date_basis=normalized_basis,
            warning="Source date is unavailable; implied dates were left blank.",
        )
    if normalized_basis == "settlement":
        trade_date, warning = shift_trading_date(source_iso, -2)
        return DateDerivation(
            ccass_date=source_iso,
            implied_trade_date=trade_date,
            implied_settlement_date=source_iso,
            date_basis=normalized_basis,
            warning=warning,
        )
    settlement_date, warning = shift_trading_date(source_iso, 2)
    return DateDerivation(
        ccass_date=source_iso,
        implied_trade_date=source_iso,
        implied_settlement_date=settlement_date,
        date_basis=normalized_basis,
        warning=warning,
    )


def date_fields(value: Any, basis: str) -> tuple[dict[str, str], str]:
    derived = derive_dates(value, basis)
    return {
        "ccass_date": derived.ccass_date,
        "implied_trade_date": derived.implied_trade_date,
        "implied_settlement_date": derived.implied_settlement_date,
        "date_basis": derived.date_basis,
    }, derived.warning


def annotate_records(
    records: list[dict[str, Any]],
    section: str,
    default_date: Any = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    basis = SECTION_DATE_BASIS.get(section, "unknown")
    output: list[dict[str, Any]] = []
    warnings: list[str] = []
    for record in records:
        row = dict(record)
        source_date = default_date
        if section in {"Big Changes", "Concentration", "Price History"}:
            source_date = row.get("Date") or row.get("date") or default_date
        fields, warning = date_fields(source_date, basis)
        row.update(fields)
        output.append(row)
        if warning and warning not in warnings:
            warnings.append(f"{section}: {warning}")
    return output, warnings


def unavailable_data_as_of(reason: str) -> str:
    detail = str(reason or "no dated CCASS section parsed").strip()
    return f"not available: {detail}"
