from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from .fetcher import FetchResult


@dataclass
class ParsedCCASS:
    stock_code: str = ""
    stock_name: str = ""
    issue_id: str = ""
    fetched_time: str = ""
    ccass_data_date: str = ""
    issued_securities: str = ""
    total_in_ccass: str = ""
    total_in_ccass_pct: str = ""
    securities_not_in_ccass: str = ""
    top5_cumulative_pct: str = ""
    top10_cumulative_pct: str = ""
    largest_participant: str = ""
    date_range: str = ""
    trading_date: str = ""
    volume: str = ""
    turnover: str = ""
    average_price: str = ""
    total_ccass_change: str = ""
    holdings_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    changes_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    big_changes_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    concentration_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    transfer_flags: list[str] = field(default_factory=list)
    page_errors: list[dict[str, str]] = field(default_factory=list)


def safe_str(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def first_match(text: str, patterns: list[str]) -> str:
    source = compact_text(text)
    for pattern in patterns:
        match = re.search(pattern, source, flags=re.I)
        if match:
            return match.group(1).strip()
    return ""


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [compact_text(str(col)) for col in out.columns]
    return out


def find_table_with_columns(tables: list[pd.DataFrame], keywords: list[str]) -> pd.DataFrame:
    for table in tables:
        df = normalize_columns(table)
        joined = " ".join(df.columns).lower()
        if all(keyword.lower() in joined for keyword in keywords):
            return df
    return normalize_columns(tables[0]) if tables else pd.DataFrame()


def pick_column(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    lower_map = {col.lower(): col for col in df.columns}
    for candidate in candidates:
        for lower, original in lower_map.items():
            if candidate.lower() in lower:
                return original
    return None


def to_number(value: Any) -> Optional[float]:
    text = safe_str(value).replace(",", "").replace("%", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def percent_text(value: Any) -> str:
    text = safe_str(value)
    if not text:
        return ""
    return text if "%" in text else f"{text}%"


def parse_holdings(result: FetchResult, parsed: ParsedCCASS) -> None:
    text = result.raw_text
    parsed.stock_code = parsed.stock_code or first_match(text, [r"Stock code[:\s]+(\d{4,5})", r"\b(\d{5})\b"])
    parsed.stock_name = parsed.stock_name or first_match(text, [r"Issue[:\s]+(.+?) Stock code", r"Name[:\s]+(.+?) Stock code"])
    parsed.ccass_data_date = first_match(text, [r"Holdings at CCASS on ([0-9]{4}-[0-9]{2}-[0-9]{2})", r"at close of business on ([0-9A-Za-z ,/-]+)"])
    parsed.issued_securities = first_match(text, [r"Issued shares?[:\s]+([0-9,]+)", r"Issued securities[:\s]+([0-9,]+)"])
    parsed.total_in_ccass = first_match(text, [r"Total number in CCASS[:\s]+([0-9,]+)", r"Total in CCASS[:\s]+([0-9,]+)"])
    parsed.total_in_ccass_pct = first_match(text, [r"Total in CCASS.*?([0-9.]+%)", r"CCASS.*?([0-9.]+%)"])
    parsed.securities_not_in_ccass = first_match(text, [r"not in CCASS[:\s]+([0-9,]+)"])

    table = find_table_with_columns(result.tables, ["participant"])
    if table.empty:
        return

    rank_col = pick_column(table, ["rank", "#"])
    participant_col = pick_column(table, ["participant", "name"])
    ccass_col = pick_column(table, ["ccass", "id"])
    holding_col = pick_column(table, ["holding", "shares", "securities"])
    stake_col = pick_column(table, ["stake", "%"])
    cumulative_col = pick_column(table, ["cumulative", "cum"])

    output = pd.DataFrame()
    output["Rank"] = table[rank_col] if rank_col else range(1, len(table) + 1)
    output["Participant"] = table[participant_col] if participant_col else ""
    output["CCASS ID"] = table[ccass_col] if ccass_col else ""
    output["Holding"] = table[holding_col] if holding_col else ""
    output["Stake %"] = table[stake_col] if stake_col else ""
    output["Cumulative %"] = table[cumulative_col] if cumulative_col else ""
    parsed.holdings_table = output.dropna(how="all")

    if not parsed.holdings_table.empty:
        parsed.largest_participant = safe_str(parsed.holdings_table.iloc[0]["Participant"])
        if len(parsed.holdings_table) >= 5:
            parsed.top5_cumulative_pct = percent_text(parsed.holdings_table.iloc[4]["Cumulative %"])
        if len(parsed.holdings_table) >= 10:
            parsed.top10_cumulative_pct = percent_text(parsed.holdings_table.iloc[9]["Cumulative %"])


def parse_changes(result: FetchResult, parsed: ParsedCCASS) -> None:
    text = result.raw_text
    parsed.date_range = first_match(text, [r"From ([0-9A-Za-z ,/-]+ to [0-9A-Za-z ,/-]+)", r"Date range[:\s]+(.+?) Trading"])
    parsed.trading_date = first_match(text, [r"Trading date[:\s]+([0-9A-Za-z ,/-]+)"])
    parsed.volume = first_match(text, [r"Volume[:\s]+([0-9,]+)"])
    parsed.turnover = first_match(text, [r"Turnover[:\s]+([$A-Z0-9,.\s]+)"])
    parsed.average_price = first_match(text, [r"Average price[:\s]+([$A-Z0-9,.]+)"])
    parsed.total_ccass_change = first_match(text, [r"Total securities in CCASS change[:\s]+([-0-9,]+)", r"Total CCASS change[:\s]+([-0-9,]+)"])

    table = find_table_with_columns(result.tables, ["participant"])
    if table.empty:
        return

    participant_col = pick_column(table, ["participant", "name"])
    change_col = pick_column(table, ["change"])
    change_pct_col = pick_column(table, ["change %", "% change", "stake change"])
    holding_after_col = pick_column(table, ["holding after", "holding"])
    stake_after_col = pick_column(table, ["stake after", "stake"])

    output = pd.DataFrame()
    output["Participant"] = table[participant_col] if participant_col else ""
    output["Change"] = table[change_col] if change_col else ""
    output["Change %"] = table[change_pct_col] if change_pct_col else ""
    output["Holding after"] = table[holding_after_col] if holding_after_col else ""
    output["Stake after"] = table[stake_after_col] if stake_after_col else ""
    parsed.changes_table = output.dropna(how="all")


def parse_big_changes(result: FetchResult, parsed: ParsedCCASS) -> None:
    table = find_table_with_columns(result.tables, ["participant"])
    if table.empty:
        return

    date_col = pick_column(table, ["date"])
    participant_col = pick_column(table, ["participant", "name"])
    change_col = pick_column(table, ["change"])
    change_pct_col = pick_column(table, ["change %", "%"])

    output = pd.DataFrame()
    output["Date"] = table[date_col] if date_col else ""
    output["Participant"] = table[participant_col] if participant_col else ""
    output["Change in shares"] = table[change_col] if change_col else ""
    output["Change %"] = table[change_pct_col] if change_pct_col else ""
    parsed.big_changes_table = output.dropna(how="all")
    parsed.transfer_flags = detect_transfer_flags(parsed.big_changes_table)


def detect_transfer_flags(df: pd.DataFrame, threshold_pct: float = 10.0) -> list[str]:
    if df.empty or "Date" not in df or "Change %" not in df:
        return []
    flags = []
    for date, group in df.groupby("Date"):
        rows = []
        for _, row in group.iterrows():
            pct = to_number(row.get("Change %"))
            if pct is not None and abs(pct) >= threshold_pct:
                rows.append((safe_str(row.get("Participant")), pct))
        positives = [item for item in rows if item[1] > 0]
        negatives = [item for item in rows if item[1] < 0]
        for pos_name, pos_pct in positives:
            for neg_name, neg_pct in negatives:
                if abs(abs(pos_pct) - abs(neg_pct)) <= 2.0:
                    flags.append(f"{date}: possible transfer, {pos_name} +{pos_pct:g}% / {neg_name} {neg_pct:g}%")
    return flags


def parse_concentration(result: FetchResult, parsed: ParsedCCASS) -> None:
    table = find_table_with_columns(result.tables, ["date"])
    if table.empty:
        return

    date_col = pick_column(table, ["date"])
    top5_col = pick_column(table, ["top 5", "top5"])
    top10_col = pick_column(table, ["top 10", "top10"])
    stake_col = pick_column(table, ["stake in ccass", "ccass", "stake"])

    output = pd.DataFrame()
    output["Date"] = table[date_col] if date_col else ""
    output["Top 5"] = table[top5_col] if top5_col else ""
    output["Top 10"] = table[top10_col] if top10_col else ""
    output["Stake in CCASS"] = table[stake_col] if stake_col else ""
    parsed.concentration_table = output.dropna(how="all")


def fallback_concentration_from_holdings(parsed: ParsedCCASS) -> None:
    if not parsed.concentration_table.empty or parsed.holdings_table.empty:
        return
    parsed.concentration_table = pd.DataFrame(
        [
            {
                "Date": parsed.ccass_data_date or "Current holdings page",
                "Top 5": parsed.top5_cumulative_pct,
                "Top 10": parsed.top10_cumulative_pct,
                "Stake in CCASS": parsed.total_in_ccass_pct,
            }
        ]
    )


def parse_results(issue_id: str, results: dict[str, FetchResult], stock_code: str = "") -> ParsedCCASS:
    parsed = ParsedCCASS(issue_id=issue_id, stock_code=stock_code)
    fetched_times = [item.fetched_time for item in results.values() if item.fetched_time]
    parsed.fetched_time = max(fetched_times) if fetched_times else ""

    for name, result in results.items():
        if not result.ok:
            parsed.page_errors.append(
                {
                    "page": name,
                    "failed_url": result.url,
                    "error_type": result.error_type,
                    "error_message": result.error_message,
                }
            )

    if results.get("Holdings") and results["Holdings"].ok:
        parse_holdings(results["Holdings"], parsed)
    if results.get("Changes") and results["Changes"].ok:
        parse_changes(results["Changes"], parsed)
    if results.get("Big Changes") and results["Big Changes"].ok:
        parse_big_changes(results["Big Changes"], parsed)
    if results.get("Concentration") and results["Concentration"].ok:
        parse_concentration(results["Concentration"], parsed)

    fallback_concentration_from_holdings(parsed)
    return parsed
