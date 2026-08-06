from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests

from .fetcher import body_head, clean_stock_code, now_iso


YAHOO_TURNOVER_WARNING = "Turnover is estimated as volume \u00d7 close, not actual turnover"
YAHOO_VWAP_WARNING = "VWAP is estimated from estimated turnover"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
YAHOO_REQUEST_HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://finance.yahoo.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}
YAHOO_REQUEST_TIMEOUT = 20


@dataclass
class YahooPriceResult:
    ok: bool
    code: str
    ticker: str
    fetched_at: str
    table: pd.DataFrame
    error_type: str = ""
    error_message: str = ""
    warning: str = YAHOO_TURNOVER_WARNING


def yahoo_ticker_from_code(stock_code: str) -> str:
    code = clean_stock_code(stock_code)
    if not code:
        return ""
    stripped = code.lstrip("0") or "0"
    symbol = stripped.zfill(4) if len(stripped) < 4 else stripped
    return f"{symbol}.HK"


def yahoo_period_for_days(period_days: int) -> str:
    days = max(1, int(period_days))
    if days <= 5:
        return "5d"
    if days <= 31:
        return "1mo"
    if days <= 93:
        return "3mo"
    if days <= 186:
        return "6mo"
    if days <= 366:
        return "1y"
    if days <= 730:
        return "2y"
    return "5y"


def yahoo_period_bounds(period_days: int = 90, start_date: str | None = None) -> tuple[int, int]:
    end_ts = int(time.time())
    if start_date:
        parsed = pd.to_datetime(start_date, errors="coerce")
        if pd.isna(parsed):
            raise ValueError("start_date must be a valid date string in YYYY-MM-DD format.")
        timestamp = pd.Timestamp(parsed)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        start_ts = int(timestamp.timestamp())
    else:
        days = max(1, int(period_days))
        start_ts = end_ts - (days * 86400)
    if start_ts >= end_ts:
        raise ValueError("start_date must be earlier than the current time.")
    return start_ts, end_ts


def yahoo_table_from_history(history: pd.DataFrame) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame(
            columns=[
                "Date",
                "Close",
                "Open",
                "High",
                "Low",
                "Volume",
                "Turnover",
                "VWAP",
                "price_source",
                "turnover_est",
                "vwap_est",
            ]
        )
    df = history.copy().reset_index()
    if "Date" not in df.columns and "Datetime" in df.columns:
        df = df.rename(columns={"Datetime": "Date"})
    out = pd.DataFrame()
    out["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for column in ["Close", "Open", "High", "Low"]:
        out[column] = pd.to_numeric(df.get(column, ""), errors="coerce").round(3)
    out["Volume"] = pd.to_numeric(df.get("Volume", ""), errors="coerce").fillna(0).astype("int64")
    close = pd.to_numeric(out["Close"], errors="coerce")
    volume = pd.to_numeric(out["Volume"], errors="coerce")
    turnover_est = (close * volume).round(2)
    out["Turnover"] = turnover_est
    out["VWAP"] = ""
    out["price_source"] = "yahoo"
    out["turnover_est"] = turnover_est
    out["vwap_est"] = (turnover_est / volume.where(volume.ne(0))).round(3)
    out = out.dropna(subset=["Date"])
    return out.sort_values("Date", ascending=False).reset_index(drop=True)


def yahoo_history_from_chart_payload(payload: dict[str, Any]) -> pd.DataFrame:
    chart = payload.get("chart") or {}
    error = chart.get("error") or {}
    if error:
        code = str(error.get("code") or "YahooAPIError")
        description = str(error.get("description") or "Yahoo Finance returned an error.")
        raise ValueError(f"{code}: {description}")

    results = chart.get("result") or []
    if not results:
        raise ValueError("Yahoo Finance returned no chart data.")

    result = results[0] or {}
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote_blocks = indicators.get("quote") or []
    quote = quote_blocks[0] if quote_blocks else {}

    rows: list[dict[str, Any]] = []
    for index, ts in enumerate(timestamps):
        if ts is None:
            continue
        date = pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Hong_Kong").strftime("%Y-%m-%d")
        row = {"Date": date}
        for column in ("Open", "High", "Low", "Close", "Volume"):
            values = quote.get(column.lower()) or []
            row[column] = values[index] if index < len(values) else None
        rows.append(row)

    if not rows:
        raise ValueError("Yahoo Finance returned no price rows.")

    history = pd.DataFrame(rows)
    history["Date"] = pd.to_datetime(history["Date"], errors="coerce")
    history = history.dropna(subset=["Date"]).set_index("Date")
    history.index.name = "Date"
    return history


def _fetch_yahoo_chart_payload(ticker: str, period_days: int) -> dict[str, Any]:
    return _fetch_yahoo_chart_payload_with_start(ticker, period_days=period_days, start_date=None)


def _fetch_yahoo_chart_payload_with_start(ticker: str, period_days: int, start_date: str | None) -> dict[str, Any]:
    url = YAHOO_CHART_URL.format(ticker=ticker)
    period1, period2 = yahoo_period_bounds(period_days=period_days, start_date=start_date)
    params = {
        "period1": period1,
        "period2": period2,
        "interval": "1d",
        "includePrePost": "false",
        "events": "div,splits",
        "formatted": "false",
    }
    response = requests.get(url, params=params, headers=YAHOO_REQUEST_HEADERS, timeout=YAHOO_REQUEST_TIMEOUT)
    text = response.text or ""
    if response.status_code != 200:
        raise ValueError(f"HTTP {response.status_code}: {body_head(text, 300) or 'Yahoo request failed.'}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError(f"Invalid JSON from Yahoo Finance: {body_head(text, 300) or exc}") from exc
    return payload


def fetch_yahoo_price_history(
    stock_code: str,
    period_days: int = 90,
    sleep_seconds: float = 0.0,
    start_date: str | None = None,
) -> YahooPriceResult:
    code = clean_stock_code(stock_code)
    ticker = yahoo_ticker_from_code(code)
    fetched_at = now_iso()
    if not ticker:
        return YahooPriceResult(False, code, ticker, fetched_at, pd.DataFrame(), "INVALID_INPUT", "Stock code is required.")
    try:
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        payload = _fetch_yahoo_chart_payload_with_start(ticker, period_days=period_days, start_date=start_date)
        history = yahoo_history_from_chart_payload(payload)
        table = yahoo_table_from_history(history)
        if table.empty:
            raise ValueError(f"Yahoo Finance returned no price rows for {ticker}.")
        return YahooPriceResult(True, code, ticker, fetched_at, table)
    except Exception as exc:
        return YahooPriceResult(False, code, ticker, fetched_at, pd.DataFrame(), type(exc).__name__, str(exc))


def fetch_latest_yahoo_price(stock_code: str, sleep_seconds: float = 0.0) -> YahooPriceResult:
    return fetch_yahoo_price_history(stock_code, period_days=7, sleep_seconds=sleep_seconds)
