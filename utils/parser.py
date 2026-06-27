from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd
from bs4 import BeautifulSoup

from .fetcher import FetchResult


SECTIONS = ["Company / orgdata", "Holdings", "Changes", "Big Changes", "Concentration", "Price History"]


@dataclass
class SectionParse:
    section: str
    selected_table_index: Optional[int] = None
    status: str = "failed"
    latest_date: str = ""
    error: str = ""
    message: str = ""


@dataclass
class ParsedCCASS:
    stock_code: str = ""
    stock_name: str = ""
    issue_id: str = ""
    id_lookup_method: str = ""
    id_lookup_status: str = ""
    fetched_time: str = ""
    holdings_data_date: str = ""
    changes_date_range: str = ""
    changes_trading_date: str = ""
    big_changes_latest_date: str = ""
    concentration_latest_date: str = ""
    price_history_latest_date: str = ""
    issued_securities: str = ""
    total_in_ccass: str = ""
    total_in_ccass_pct: str = ""
    securities_not_in_ccass: str = ""
    largest_participant: str = ""
    top5_cumulative_pct: str = ""
    top10_cumulative_pct: str = ""
    volume: str = ""
    turnover: str = ""
    average_price: str = ""
    latest_price: str = ""
    latest_price_turnover: str = ""
    latest_price_volume: str = ""
    latest_price_vwap: str = ""
    total_ccass_change: str = ""
    major_increases: list[str] = field(default_factory=list)
    major_decreases: list[str] = field(default_factory=list)
    changes_flags: list[str] = field(default_factory=list)
    concentration_5day_change: dict[str, str] = field(default_factory=dict)
    transfer_flags: list[str] = field(default_factory=list)
    analysis_warnings: list[str] = field(default_factory=list)
    company_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    holdings_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    changes_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    big_changes_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    concentration_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    price_history_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    section_parses: dict[str, SectionParse] = field(default_factory=dict)


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9%+]+", " ", safe_str(text).lower()).strip()


def table_text(df: pd.DataFrame, rows: int = 5) -> str:
    if df is None or df.empty:
        return ""
    sample = df.head(rows).astype(str).to_string(index=False)
    return norm(" ".join(map(str, df.columns)) + " " + sample)


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


def pick_column(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    for candidate in candidates:
        candidate_norm = norm(candidate)
        for col in df.columns:
            col_norm = norm(col)
            if candidate_norm and candidate_norm in col_norm:
                return col
    return None


def pick_first_column(df: pd.DataFrame, groups: list[list[str]]) -> Optional[str]:
    for group in groups:
        col = pick_column(df, group)
        if col:
            return col
    return None


def to_number(value: Any) -> Optional[float]:
    text = safe_str(value).replace(",", "").replace("%", "")
    text = text.replace("+", "")
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


def parse_date_value(value: Any) -> Optional[pd.Timestamp]:
    text = safe_str(value)
    if not text:
        return None
    short = re.fullmatch(r"(\d{2})-(\d{2})-(\d{2})", text)
    if short:
        year, month, day = short.groups()
        parsed = pd.to_datetime(f"20{year}-{month}-{day}", errors="coerce")
        return None if pd.isna(parsed) else parsed
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed


def latest_date_from_column(df: pd.DataFrame, column: str) -> str:
    dates = [parse_date_value(value) for value in df[column].tolist()]
    dates = [date for date in dates if date is not None]
    if not dates:
        return ""
    return max(dates).strftime("%Y-%m-%d")


def table_preview_records(results: dict[str, FetchResult]) -> list[dict[str, Any]]:
    records = []
    for section, result in results.items():
        for idx, table in enumerate(result.tables, start=1):
            df = normalize_columns(table)
            records.append(
                {
                    "section": section,
                    "table_index": idx,
                    "shape": f"{df.shape[0]} x {df.shape[1]}",
                    "columns": list(map(str, df.columns)),
                    "preview": df.head(3).fillna("").astype(str).to_dict(orient="records"),
                }
            )
    return records


def score_table(section: str, df: pd.DataFrame) -> int:
    text = table_text(df)
    if not text:
        return 0

    rules = {
        "Holdings": [
            "participant",
            "name",
            "ccass id",
            "holding",
            "stake",
            "cumulative",
            "cumul stake",
            "name of ccass participant",
            "holding percentage",
            "cumulative percentage",
        ],
        "Changes": [
            "participant",
            "name",
            "change",
            "holding",
            "stake",
            "holding after",
            "change in shares",
            "change %",
            "stake",
            "trading date",
            "volume",
            "turnover",
            "average price",
            "total securities in ccass",
        ],
        "Big Changes": ["date", "participant", "change", "change %", "holding change"],
        "Concentration": ["date", "top 5", "top 10", "top 10 ncip", "stake in ccass"],
        "Price History": ["date", "close", "price", "volume", "turnover", "vwap"],
        "Company / orgdata": ["code", "listed", "hk main", "stock code", "name", "issue"],
    }
    score = sum(1 for keyword in rules.get(section, []) if norm(keyword) in text)

    if section in {"Holdings", "Changes"} and "participant" not in text and "name" not in text:
        return 0
    if section == "Big Changes" and "participant" not in text:
        return 0
    if section == "Holdings" and not all(token in text for token in ("holding", "stake")):
        return 0
    if section == "Changes" and "change" not in text:
        return 0
    if section == "Big Changes" and not ("date" in text and "change" in text):
        return 0
    if section == "Concentration" and not ("date" in text and ("top 5" in text or "top5" in text)):
        return 0
    if section == "Price History" and not ("date" in text and ("close" in text or "price" in text)):
        return 0
    return score


def auto_select_table(section: str, tables: list[pd.DataFrame]) -> Optional[int]:
    best_index = None
    best_score = 0
    for idx, table in enumerate(tables):
        score = score_table(section, normalize_columns(table))
        if score > best_score:
            best_score = score
            best_index = idx
    minimum = 2 if section == "Company / orgdata" else 3
    return best_index if best_score >= minimum else None


def get_selected_table(section: str, result: FetchResult, overrides: dict[str, int] | None, parse: SectionParse) -> pd.DataFrame:
    if not result or not result.tables:
        parse.status = "failed" if result and not result.ok else "no matching table"
        parse.error = result.error_message if result else "not fetched"
        return pd.DataFrame()

    selected = overrides.get(section) if overrides else None
    if selected is None:
        selected = auto_select_table(section, result.tables)
    else:
        parse.status = "manually selected"

    if selected is None or selected < 0 or selected >= len(result.tables):
        parse.status = "no matching table"
        parse.error = f"{section} table parsing failed. Raw table previews are shown below."
        return pd.DataFrame()

    parse.selected_table_index = selected + 1
    if parse.status != "manually selected":
        parse.status = "success"
    return normalize_columns(result.tables[selected])


def parse_company(result: FetchResult, parsed: ParsedCCASS, overrides: dict[str, int] | None) -> None:
    parse = SectionParse("Company / orgdata")
    parsed.section_parses[parse.section] = parse
    table = get_selected_table(parse.section, result, overrides, parse)
    parsed.company_table = table
    text = result.raw_text if result else ""
    if result and result.html:
        soup = BeautifulSoup(result.html, "lxml")
        heading = soup.find("h1")
        if heading:
            parsed.stock_name = heading.get_text(" ", strip=True)
        elif soup.title:
            parsed.stock_name = soup.title.get_text(" ", strip=True)
    parsed.stock_code = parsed.stock_code or first_match(text, [r"\bStock code[:\s]+(\d{4,5})", r"\bCode[:\s]+(\d{4,5})"])
    parsed.stock_name = parsed.stock_name or first_match(text, [r"\bName[:\s]+(.+?)\s+(?:Code|Stock code|Market)", r"\bIssue[:\s]+(.+?)\s+(?:Code|Stock code)"])
    if not parsed.stock_name and not table.empty:
        joined = " ".join(table.head(3).fillna("").astype(str).to_numpy().ravel().tolist())
        parsed.stock_name = first_match(joined, [r"Name\s+(.+?)\s+Code", r"Issue\s+(.+?)\s+Code"])
    if result and result.ok and parse.status == "failed":
        parse.status = "success"


def parse_holdings(result: FetchResult, parsed: ParsedCCASS, overrides: dict[str, int] | None) -> None:
    parse = SectionParse("Holdings")
    parsed.section_parses[parse.section] = parse
    table = get_selected_table(parse.section, result, overrides, parse)
    if table.empty:
        return

    text = result.raw_text
    parsed.stock_code = parsed.stock_code or first_match(text, [r"\bStock code[:\s]+(\d{4,5})", r"\bCode[:\s]+(\d{4,5})"])
    parsed.stock_name = parsed.stock_name or first_match(text, [r"\bIssue[:\s]+(.+?)\s+(?:Stock code|Code)", r"\bName[:\s]+(.+?)\s+(?:Stock code|Code)"])
    parsed.holdings_data_date = first_match(
        text,
        [
            r"CCASS holdings on ([0-9]{4}-[0-9]{2}-[0-9]{2})",
            r"Holdings at CCASS on ([0-9]{4}-[0-9]{2}-[0-9]{2})",
            r"at close of business on ([0-9A-Za-z ,/-]+)",
            r"Shareholding Date[:\s]+([0-9A-Za-z ,/-]+)",
        ],
    )
    parsed.issued_securities = first_match(text, [r"Issued shares?[:\s]+([0-9,]+)", r"Issued securities[:\s]+([0-9,]+)"])
    parsed.total_in_ccass = first_match(text, [r"Total (?:number )?in CCASS[:\s]+([0-9,]+)", r"Total securities in CCASS[:\s]+([0-9,]+)"])
    parsed.total_in_ccass_pct = first_match(text, [r"Total (?:number )?in CCASS.*?([0-9.]+%)", r"Stake in CCASS[:\s]+([0-9.]+%)"])
    parsed.securities_not_in_ccass = first_match(text, [r"(?:Securities )?not in CCASS[:\s]+([0-9,]+)"])
    holder_table = next((normalize_columns(tbl) for tbl in result.tables if "Type of holder" in map(str, tbl.columns)), pd.DataFrame())
    if not holder_table.empty and {"Type of holder", "Holding"}.issubset(set(map(str, holder_table.columns))):
        total_rows = holder_table[holder_table["Type of holder"].astype(str).str.contains("Total", case=False, na=False)]
        outside_rows = holder_table[holder_table["Type of holder"].astype(str).str.contains("not in CCASS|outside", case=False, na=False)]
        if not total_rows.empty:
            parsed.total_in_ccass = parsed.total_in_ccass or safe_str(total_rows.iloc[0].get("Holding"))
            parsed.total_in_ccass_pct = parsed.total_in_ccass_pct or percent_text(total_rows.iloc[0].get("Stake %", ""))
        if not outside_rows.empty:
            parsed.securities_not_in_ccass = parsed.securities_not_in_ccass or safe_str(outside_rows.iloc[0].get("Holding"))

    rank_col = pick_first_column(table, [["rank"], ["#"]])
    participant_col = pick_first_column(table, [["name of ccass participant"], ["participant"], ["name"]])
    ccass_col = pick_first_column(table, [["ccass id"], ["participant id"], ["id"]])
    holding_col = pick_first_column(table, [["holding"], ["shares"], ["securities"]])
    stake_col = pick_first_column(table, [["stake %"], ["holding percentage"], ["stake"], ["%"]])
    cumulative_col = pick_first_column(table, [["cumul stake"], ["cumulative percentage"], ["cumulative %"], ["cumulative"], ["cum"]])

    required = [participant_col, holding_col, stake_col]
    if any(col is None for col in required):
        parse.status = "no matching table"
        parse.error = "Holdings table parsing failed. Raw table previews are shown below."
        return

    output = pd.DataFrame()
    output["Rank"] = table[rank_col] if rank_col else range(1, len(table) + 1)
    output["Participant"] = table[participant_col]
    output["CCASS ID"] = table[ccass_col] if ccass_col else ""
    output["Holding"] = table[holding_col]
    output["Stake %"] = table[stake_col]
    output["Cumulative %"] = table[cumulative_col] if cumulative_col else ""
    output = output.dropna(how="all")
    output = output[output["Participant"].astype(str).str.strip().ne("")]
    parsed.holdings_table = output

    if parsed.holdings_table.empty:
        parse.status = "no matching table"
        parse.error = "Holdings table parsing failed. Raw table previews are shown below."
        return

    parse.latest_date = parsed.holdings_data_date
    parsed.largest_participant = safe_str(parsed.holdings_table.iloc[0]["Participant"])
    if len(parsed.holdings_table) >= 5:
        parsed.top5_cumulative_pct = percent_text(parsed.holdings_table.iloc[4]["Cumulative %"])
    if len(parsed.holdings_table) >= 10:
        parsed.top10_cumulative_pct = percent_text(parsed.holdings_table.iloc[9]["Cumulative %"])


def parse_changes(result: FetchResult, parsed: ParsedCCASS, overrides: dict[str, int] | None) -> None:
    parse = SectionParse("Changes")
    parsed.section_parses[parse.section] = parse
    table = get_selected_table(parse.section, result, overrides, parse)
    if table.empty:
        return

    text = result.raw_text
    parsed.changes_date_range = first_match(text, [r"From ([0-9]{4}-[0-9]{2}-[0-9]{2} to [0-9]{4}-[0-9]{2}-[0-9]{2})", r"Date range[:\s]+([0-9]{4}-[0-9]{2}-[0-9]{2}.+?[0-9]{4}-[0-9]{2}-[0-9]{2})"])
    parsed.changes_trading_date = first_match(text, [r"Trading date[:\s]+([0-9]{4}-[0-9]{2}-[0-9]{2})"])
    parsed.volume = first_match(text, [r"Volume[:\s]+([0-9,]+)"])
    parsed.turnover = first_match(text, [r"Turnover[:\s]+([$A-Z0-9,.\s]+)"])
    parsed.average_price = first_match(text, [r"Average price[:\s]+([$A-Z0-9,.]+)"])
    parsed.total_ccass_change = first_match(text, [r"Total securities in CCASS change[:\s]+([-+0-9,]+)", r"Total CCASS change[:\s]+([-+0-9,]+)"])
    for raw_table in result.tables:
        kv_table = normalize_columns(raw_table)
        if kv_table.shape[1] < 2:
            continue
        first_col, second_col = kv_table.columns[0], kv_table.columns[1]
        pairs = {safe_str(row[first_col]).lower(): safe_str(row[second_col]) for _, row in kv_table.iterrows()}
        parsed.changes_trading_date = pairs.get("trading date", parsed.changes_trading_date)
        parsed.volume = pairs.get("volume", parsed.volume)
        parsed.turnover = pairs.get("turnover", parsed.turnover)
        parsed.average_price = pairs.get("average price", parsed.average_price)

    participant_col = pick_first_column(table, [["participant"], ["name of ccass participant"], ["name"]])
    change_col = pick_first_column(table, [["change in shares"], ["change"]])
    change_pct_col = next((col for col in table.columns if "Δ" in str(col) or "delta" in norm(col)), None)
    change_pct_col = change_pct_col or pick_first_column(table, [["change %"], ["% change"], ["stake change"]])
    holding_after_col = pick_first_column(table, [["holding after"], ["holding"]])
    stake_after_col = pick_first_column(table, [["stake after"], ["stake"]])

    if any(col is None for col in [participant_col, change_col]):
        parse.status = "no matching table"
        parse.error = "Changes table parsing failed. Raw table previews are shown below."
        return

    output = pd.DataFrame()
    output["Participant"] = table[participant_col]
    output["Change"] = table[change_col]
    output["Change %"] = table[change_pct_col] if change_pct_col else table[change_col]
    output["Holding after"] = table[holding_after_col] if holding_after_col else ""
    output["Stake after"] = table[stake_after_col] if stake_after_col else ""
    output = output.dropna(how="all")
    output = output[output["Participant"].astype(str).str.strip().ne("")]
    parsed.changes_table = output

    if parsed.changes_table.empty:
        parse.status = "no matching table"
        parse.error = "Changes table parsing failed. Raw table previews are shown below."
        return

    parse.latest_date = parsed.changes_trading_date or parsed.changes_date_range
    ranked = parsed.changes_table.assign(_change=parsed.changes_table["Change"].map(to_number)).dropna(subset=["_change"])
    increases = ranked.sort_values("_change", ascending=False).head(5)
    decreases = ranked.sort_values("_change", ascending=True).head(5)
    parsed.major_increases = [f"{row['Participant']}: {row['Change']}" for _, row in increases.iterrows() if row["_change"] > 0]
    parsed.major_decreases = [f"{row['Participant']}: {row['Change']}" for _, row in decreases.iterrows() if row["_change"] < 0]
    parsed.changes_flags = classify_changes(parsed.changes_table)


def classify_changes(df: pd.DataFrame) -> list[str]:
    if df.empty:
        return []
    broker_keywords = ("securities", "sec", "brokerage", "capital", "futu", "uob", "kingston", "rifa", "phillip", "bright")
    bank_keywords = ("bank", "nominees", "custodian", "clearing", "hkscc", "central clearing")
    ranked = df.copy()
    ranked["_change"] = ranked["Change"].map(to_number)
    ranked["_stake_after"] = ranked["Stake after"].map(to_number)
    ranked = ranked.dropna(subset=["_change"])
    if ranked.empty:
        return []

    flags: list[str] = []
    increases = ranked[ranked["_change"] > 0].sort_values("_change", ascending=False)
    decreases = ranked[ranked["_change"] < 0].sort_values("_change", ascending=True)
    total_inc = increases["_change"].sum()
    total_dec = abs(decreases["_change"].sum())

    large_increases = increases[increases["_stake_after"].fillna(0) >= 1]
    retail_like_increases = increases[
        increases["Participant"].astype(str).str.lower().apply(lambda name: any(k in name for k in broker_keywords))
    ]
    custody_like_increases = increases[
        increases["Participant"].astype(str).str.lower().apply(lambda name: any(k in name for k in bank_keywords))
    ]

    if not large_increases.empty:
        sample = ", ".join(large_increases.head(3)["Participant"].astype(str).tolist())
        flags.append(f"大戶券商增倉: {sample}")
    if not retail_like_increases.empty:
        sample = ", ".join(retail_like_increases.head(5)["Participant"].astype(str).tolist())
        flags.append(f"散戶券商增倉: {sample}")
    if not decreases.empty and abs(decreases.iloc[0]["_change"]) >= max(total_dec * 0.5, 1):
        flags.append(f"單一大戶減倉: {decreases.iloc[0]['Participant']} {decreases.iloc[0]['Change']}")
    if len(increases) >= 4 and total_inc > 0:
        flags.append(f"多間券商分散承接: {len(increases)} participants increased holdings")
    if total_inc > 0 and total_dec > 0 and abs(total_inc - total_dec) <= max(total_inc, total_dec) * 0.15:
        flags.append("是否疑似轉倉: yes, increases and decreases are broadly balanced")
    elif not custody_like_increases.empty and total_dec > 0:
        flags.append("是否疑似轉倉: possible, custody-like participant increased while others decreased")
    else:
        flags.append("是否疑似轉倉: not confirmed from Changes table alone")
    return flags


def parse_big_changes(result: FetchResult, parsed: ParsedCCASS, overrides: dict[str, int] | None) -> None:
    parse = SectionParse("Big Changes")
    parsed.section_parses[parse.section] = parse
    table = get_selected_table(parse.section, result, overrides, parse)
    if table.empty:
        return

    date_col = pick_first_column(table, [["date"]])
    participant_col = pick_first_column(table, [["participant"], ["name"]])
    shares_col = pick_first_column(table, [["change in shares"], ["shares"], ["holding change"]])
    change_col = shares_col or pick_first_column(table, [["change"]])
    change_pct_col = pick_first_column(table, [["change %"], ["% change"], ["%"]])
    if any(col is None for col in [date_col, participant_col, change_col]):
        parse.status = "no matching table"
        parse.error = "Big Changes table parsing failed. Raw table previews are shown below."
        return

    output = pd.DataFrame()
    output["Raw Date"] = table[date_col]
    output["Date"] = table[date_col].replace("", pd.NA).ffill()
    output["Participant"] = table[participant_col]
    if shares_col:
        output["Change in shares"] = table[shares_col]
        output["Change %"] = table[change_pct_col] if change_pct_col else ""
    else:
        output["Change %"] = table[change_pct_col] if change_pct_col else table[change_col]
    output = output.dropna(how="all")
    output = output[output["Participant"].astype(str).str.strip().ne("")]
    columns = ["Date", "Participant"]
    if "Change in shares" in output.columns:
        columns.append("Change in shares")
    columns.append("Change %")
    parsed.big_changes_table = output[columns]

    if parsed.big_changes_table.empty:
        parse.status = "no matching table"
        parse.error = "Big Changes table parsing failed. Raw table previews are shown below."
        return

    parsed.big_changes_latest_date = latest_date_from_column(parsed.big_changes_table, "Date")
    parse.latest_date = parsed.big_changes_latest_date
    parsed.transfer_flags = detect_transfer_flags(parsed.big_changes_table)


def detect_transfer_flags(df: pd.DataFrame, threshold_pct: float = 10.0) -> list[str]:
    if df.empty or "Date" not in df or "Change %" not in df:
        return []
    flags = []
    for date, group in df.groupby("Date", dropna=True):
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
                    flags.append(
                        f"{date}: possible large custody transfer / warehouse transfer, {pos_name} +{pos_pct:g}% / {neg_name} {neg_pct:g}%"
                    )
    return flags


def parse_concentration(result: FetchResult, parsed: ParsedCCASS, overrides: dict[str, int] | None) -> None:
    parse = SectionParse("Concentration")
    parsed.section_parses[parse.section] = parse
    table = get_selected_table(parse.section, result, overrides, parse)
    if table.empty:
        return

    date_col = pick_first_column(table, [["date"]])
    top5_col = pick_first_column(table, [["top 5"], ["top5"]])
    top10_ncip_col = pick_first_column(table, [["top 10 + ncip"], ["top 10 ncip"], ["ncip"]])
    top10_col = pick_first_column(table, [["top 10"], ["top10"]])
    stake_col = pick_first_column(table, [["stake in ccass"], ["ccass"], ["stake"]])
    if any(col is None for col in [date_col, top5_col, top10_col]):
        parse.status = "no matching table"
        parse.error = "Concentration table parsing failed. Raw table previews are shown below."
        return

    output = pd.DataFrame()
    output["Date"] = table[date_col]
    output["Top 5 %"] = table[top5_col]
    output["Top 10 %"] = table[top10_col]
    output["Top 10 + NCIP %"] = table[top10_ncip_col] if top10_ncip_col else ""
    output["Stake in CCASS %"] = table[stake_col] if stake_col else ""
    output = output.dropna(how="all")
    parsed.concentration_table = output

    if parsed.concentration_table.empty:
        parse.status = "no matching table"
        parse.error = "Concentration table parsing failed. Raw table previews are shown below."
        return

    parsed.concentration_latest_date = latest_date_from_column(parsed.concentration_table, "Date")
    parse.latest_date = parsed.concentration_latest_date
    parsed.concentration_5day_change = calculate_concentration_5day_change(parsed.concentration_table)
    validate_concentration(parsed)


def parse_price_history(result: FetchResult, parsed: ParsedCCASS, overrides: dict[str, int] | None) -> None:
    parse = SectionParse("Price History")
    parsed.section_parses[parse.section] = parse
    table = get_selected_table(parse.section, result, overrides, parse)
    if table.empty:
        return

    date_col = pick_first_column(table, [["date"]])
    close_col = pick_first_column(table, [["close"], ["price"]])
    volume_col = pick_first_column(table, [["volume"], ["vol"]])
    turnover_col = pick_first_column(table, [["turnover"], ["value"], ["amount"]])
    vwap_col = pick_first_column(table, [["vwap"], ["average price"], ["avg price"]])
    high_col = pick_first_column(table, [["high"]])
    low_col = pick_first_column(table, [["low"]])
    open_col = pick_first_column(table, [["open"]])

    if any(col is None for col in [date_col, close_col]):
        parse.status = "no matching table"
        parse.error = "Price History table parsing failed. Raw table previews are shown below."
        return

    output = pd.DataFrame()
    output["Date"] = table[date_col]
    output["Close"] = table[close_col]
    output["Open"] = table[open_col] if open_col else ""
    output["High"] = table[high_col] if high_col else ""
    output["Low"] = table[low_col] if low_col else ""
    output["Volume"] = table[volume_col] if volume_col else ""
    output["Turnover"] = table[turnover_col] if turnover_col else ""
    output["VWAP"] = table[vwap_col] if vwap_col else ""
    output = output.dropna(how="all")
    output = output[output["Date"].astype(str).str.strip().ne("")]
    parsed.price_history_table = output

    if parsed.price_history_table.empty:
        parse.status = "no matching table"
        parse.error = "Price History table parsing failed. Raw table previews are shown below."
        return

    parsed.price_history_latest_date = latest_date_from_column(parsed.price_history_table, "Date")
    parse.latest_date = parsed.price_history_latest_date
    sorted_df = parsed.price_history_table.copy()
    sorted_df["_date"] = sorted_df["Date"].map(parse_date_value)
    sorted_df = sorted_df.dropna(subset=["_date"]).sort_values("_date", ascending=False)
    if not sorted_df.empty:
        latest = sorted_df.iloc[0]
        parsed.latest_price = safe_str(latest.get("Close"))
        parsed.latest_price_volume = safe_str(latest.get("Volume"))
        parsed.latest_price_turnover = safe_str(latest.get("Turnover"))
        parsed.latest_price_vwap = safe_str(latest.get("VWAP"))


def calculate_concentration_5day_change(df: pd.DataFrame) -> dict[str, str]:
    if df.empty or len(df) < 2:
        return {}
    sorted_df = df.copy()
    sorted_df["_date"] = sorted_df["Date"].map(parse_date_value)
    sorted_df = sorted_df.dropna(subset=["_date"]).sort_values("_date", ascending=False)
    if len(sorted_df) < 2:
        return {}
    latest = sorted_df.iloc[0]
    base = sorted_df.iloc[min(4, len(sorted_df) - 1)]
    changes = {}
    for label in ["Top 5 %", "Top 10 %", "Stake in CCASS %"]:
        latest_value = to_number(latest.get(label))
        base_value = to_number(base.get(label))
        if latest_value is None or base_value is None:
            changes[label] = "not available"
        else:
            changes[label] = f"{latest_value - base_value:+.2f} ppt ({safe_str(base.get('Date'))} to {safe_str(latest.get('Date'))})"
    return changes


def validate_concentration(parsed: ParsedCCASS) -> None:
    if parsed.concentration_table.empty:
        return
    value_columns = ["Top 5 %", "Top 10 %", "Top 10 + NCIP %", "Stake in CCASS %"]
    for idx, row in parsed.concentration_table.iterrows():
        date = safe_str(row.get("Date")) or f"row {idx + 1}"
        for column in value_columns:
            value = to_number(row.get(column))
            if value is None:
                continue
            if value < 0 or value > 100:
                parsed.analysis_warnings.append(
                    f"Abnormal concentration value: {date} {column} = {row.get(column)}. Expected range is 0-100."
                )


def fallback_concentration_from_holdings(parsed: ParsedCCASS, result: FetchResult | None) -> None:
    if not parsed.concentration_table.empty or parsed.holdings_table.empty:
        return
    parsed.concentration_table = pd.DataFrame(
        [
            {
                "Date": parsed.holdings_data_date or "Current holdings page",
                "Top 5 %": parsed.top5_cumulative_pct,
                "Top 10 %": parsed.top10_cumulative_pct,
                "Top 10 + NCIP %": "",
                "Stake in CCASS %": parsed.total_in_ccass_pct,
            }
        ]
    )
    parsed.concentration_latest_date = parsed.holdings_data_date or "Current holdings page"
    section = parsed.section_parses.setdefault("Concentration", SectionParse("Concentration"))
    section.status = "partial success"
    section.latest_date = parsed.concentration_latest_date
    section.error = "Concentration page failed; Top 5 / Top 10 estimated from Holdings table."
    if result:
        section.selected_table_index = None


def unavailable(value: str, reason: str) -> str:
    return value if value else f"not available because {reason}"


def add_cross_section_warnings(parsed: ParsedCCASS) -> None:
    if not parsed.concentration_table.empty and parsed.holdings_table.empty:
        parsed.analysis_warnings.append("Concentration succeeded, but Holdings failed. Full broker-level analysis is incomplete.")
    if not parsed.big_changes_table.empty and parsed.changes_table.empty:
        parsed.analysis_warnings.append("Big Changes succeeded, but daily Changes failed. Recent daily movement cannot be confirmed.")


def parse_results(
    issue_id: str,
    results: dict[str, FetchResult],
    stock_code: str = "",
    id_lookup_method: str = "",
    id_lookup_status: str = "",
    selected_indices: dict[str, int] | None = None,
) -> ParsedCCASS:
    parsed = ParsedCCASS(
        issue_id=issue_id,
        stock_code=stock_code,
        id_lookup_method=id_lookup_method,
        id_lookup_status=id_lookup_status,
    )
    fetched_times = [item.fetched_time for item in results.values() if item.fetched_time]
    parsed.fetched_time = max(fetched_times) if fetched_times else ""

    if results.get("Company / orgdata"):
        parse_company(results["Company / orgdata"], parsed, selected_indices)
    if results.get("Holdings"):
        if results["Holdings"].ok:
            parse_holdings(results["Holdings"], parsed, selected_indices)
        else:
            parsed.section_parses["Holdings"] = SectionParse("Holdings", status="failed", error=results["Holdings"].error_message)
    if results.get("Changes"):
        if results["Changes"].ok:
            parse_changes(results["Changes"], parsed, selected_indices)
        else:
            parsed.section_parses["Changes"] = SectionParse("Changes", status="failed", error=results["Changes"].error_message)
    if results.get("Big Changes"):
        if results["Big Changes"].ok:
            parse_big_changes(results["Big Changes"], parsed, selected_indices)
        else:
            parsed.section_parses["Big Changes"] = SectionParse("Big Changes", status="failed", error=results["Big Changes"].error_message)
    if results.get("Concentration"):
        if results["Concentration"].ok:
            parse_concentration(results["Concentration"], parsed, selected_indices)
        else:
            parsed.section_parses["Concentration"] = SectionParse("Concentration", status="failed", error=results["Concentration"].error_message)
    if results.get("Price History"):
        if results["Price History"].ok:
            parse_price_history(results["Price History"], parsed, selected_indices)
        else:
            parsed.section_parses["Price History"] = SectionParse("Price History", status="failed", error=results["Price History"].error_message)

    fallback_concentration_from_holdings(parsed, results.get("Concentration"))
    add_cross_section_warnings(parsed)
    return parsed


def build_fetch_summary(parsed: ParsedCCASS, results: dict[str, FetchResult]) -> pd.DataFrame:
    rows = []
    for section in SECTIONS:
        result = results.get(section)
        parse = parsed.section_parses.get(section, SectionParse(section))
        status = parse.status
        if result and not result.ok:
            status = "failed"
        rows.append(
            {
                "Section": section,
                "URL": result.url if result else "",
                "Status": status,
                "Tables found": len(result.tables) if result else 0,
                "Selected table index": parse.selected_table_index if parse.selected_table_index is not None else "",
                "Latest date / data date": parse.latest_date,
                "Error": parse.error or (result.error_message if result and not result.ok else ""),
            }
        )
    return pd.DataFrame(rows)
