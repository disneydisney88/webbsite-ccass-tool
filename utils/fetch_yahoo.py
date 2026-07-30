from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .fetcher import clean_stock_code, now_iso


YAHOO_TURNOVER_WARNING = "Turnover is estimated as volume \u00d7 close, not actual turnover"


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


def yahoo_table_from_history(history: pd.DataFrame) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame(columns=["Date", "Close", "Open", "High", "Low", "Volume", "Turnover", "VWAP", "price_source", "turnover_est"])
    df = history.copy().reset_index()
    if "Date" not in df.columns and "Datetime" in df.columns:
        df = df.rename(columns={"Datetime": "Date"})
    out = pd.DataFrame()
    out["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    out["Close"] = df.get("Close", "")
    out["Open"] = df.get("Open", "")
    out["High"] = df.get("High", "")
    out["Low"] = df.get("Low", "")
    out["Volume"] = df.get("Volume", "")
    close = pd.to_numeric(out["Close"], errors="coerce")
    volume = pd.to_numeric(out["Volume"], errors="coerce")
    turnover_est = (close * volume).round(2)
    out["Turnover"] = turnover_est
    out["VWAP"] = ""
    out["price_source"] = "yahoo"
    out["turnover_est"] = turnover_est
    out = out.dropna(subset=["Date"])
    return out.sort_values("Date", ascending=False).reset_index(drop=True)


def fetch_yahoo_price_history(stock_code: str, period_days: int = 90, sleep_seconds: float = 0.0) -> YahooPriceResult:
    code = clean_stock_code(stock_code)
    ticker = yahoo_ticker_from_code(code)
    fetched_at = now_iso()
    if not ticker:
        return YahooPriceResult(False, code, ticker, fetched_at, pd.DataFrame(), "INVALID_INPUT", "Stock code is required.")
    try:
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        import yfinance as yf

        ticker_obj = yf.Ticker(ticker)
        history = ticker_obj.history(period=yahoo_period_for_days(period_days), interval="1d", auto_adjust=False, actions=False)
        table = yahoo_table_from_history(history)
        if table.empty:
            raise ValueError(f"Yahoo Finance returned no price rows for {ticker}.")
        return YahooPriceResult(True, code, ticker, fetched_at, table)
    except Exception as exc:
        return YahooPriceResult(False, code, ticker, fetched_at, pd.DataFrame(), type(exc).__name__, str(exc))


def fetch_latest_yahoo_price(stock_code: str, sleep_seconds: float = 0.0) -> YahooPriceResult:
    return fetch_yahoo_price_history(stock_code, period_days=7, sleep_seconds=sleep_seconds)
