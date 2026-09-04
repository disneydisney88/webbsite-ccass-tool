from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

import pandas as pd
from bs4 import BeautifulSoup

from .date_semantics import (
    SECTION_DATE_BASIS,
    SETTLEMENT_NOTE,
    annotate_records,
    derive_dates,
    shift_trading_date,
    trading_sessions_between,
    unavailable_data_as_of,
)
from .fetcher import FetchResult, clean_stock_code


SECTIONS = ["Company / orgdata", "Holdings", "Changes", "Big Changes", "Concentration", "Price History"]
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CCASS_ID_RE = re.compile(r"[A-Ca-c]\d{5}")


class DateSanityError(ValueError):
    """Raised when a parsed source date is impossible for the security."""


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
    source: str = ""
    mirror_status: str = ""
    mirror_base_url: str = ""
    history_depth_days: int = 0
    db_restored_from_backup: bool = False
    db_snapshot_id: str = ""
    db_updated_at: str = ""
    db_latest_snapshot_date: str = ""
    db_latest_price_date: str = ""
    db_snapshot_rows: int = 0
    db_price_rows: int = 0
    fetched_time: str = ""
    listing_date: str = ""
    holdings_data_date: str = ""
    holdings_implied_trade_date: str = ""
    changes_date_range: str = ""
    changes_trading_date: str = ""
    changes_implied_trade_date: str = ""
    big_changes_latest_date: str = ""
    big_changes_implied_trade_date: str = ""
    concentration_latest_date: str = ""
    concentration_implied_trade_date: str = ""
    price_history_latest_date: str = ""
    data_as_of_trading_date: str = ""
    date_basis_by_section: dict[str, str] = field(default_factory=dict)
    section_asof: dict[str, dict[str, Any]] = field(default_factory=dict)
    settlement_note: str = SETTLEMENT_NOTE
    default_pct_basis: str = "issued"
    completeness_status: str = "complete"
    critical_sections_failed: list[str] = field(default_factory=list)
    section_total_counts: dict[str, int] = field(default_factory=dict)
    non_ccass_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    price_source: str = ""
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


def clean_ccass_id(value: Any) -> str:
    match = CCASS_ID_RE.search(safe_str(value))
    return match.group(0).upper() if match else ""


def clean_participant_name(value: Any) -> str:
    text = compact_text(safe_str(value))
    text = re.sub(
        r"^name\s+of\s+ccass\s+participant\s*"
        r"\(\*\s*for\s*consenting\s+investor\s+participants\s*\)\s*:?\s*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"^participant(?:\s+name|\s+id)?\s*:?\s*", "", text, flags=re.I)
    trailing_the = re.fullmatch(r"(.+?)\s*\(THE\)", text, flags=re.I)
    if trailing_the:
        text = f"THE {trailing_the.group(1).strip()}"
    if text.upper() == "THE HONGKONG AND SHANGHAI BANKING":
        return "THE HONGKONG AND SHANGHAI BANKING CORPORATION LIMITED"
    return text


def participant_name_key(value: Any, strip_suffixes: bool = False) -> str:
    text = clean_participant_name(value).upper()
    text = re.sub(r"\(THE\)\s*$", "", text).strip()
    text = re.sub(r"^THE\s+", "", text)
    text = re.sub(r"[^A-Z0-9]+", " ", text).strip()
    if strip_suffixes:
        suffixes = (
            " CORPORATION LIMITED",
            " COMPANY LIMITED",
            " CO LIMITED",
            " LIMITED",
            " LTD",
        )
        changed = True
        while changed:
            changed = False
            for suffix in suffixes:
                if text.endswith(suffix):
                    text = text[: -len(suffix)].strip()
                    changed = True
                    break
    return text


def participant_directory_map(result: FetchResult | None) -> dict[str, tuple[str, str]]:
    """Return exact and suffix-insensitive name keys -> (CCASS ID, name)."""
    if result is None or not result.ok:
        return {}
    candidates: list[tuple[str, str]] = []
    for raw_table in result.tables:
        table = normalize_columns(raw_table)
        id_col = pick_first_column(table, [["ccass id"], ["participant id"]])
        name_col = pick_first_column(table, [["name"], ["participant"]])
        if not id_col or not name_col:
            continue
        for _, row in table.iterrows():
            ccass_id = clean_ccass_id(row.get(id_col))
            name = clean_participant_name(row.get(name_col))
            if ccass_id and name:
                candidates.append((ccass_id, name))

    mapping: dict[str, tuple[str, str]] = {}
    base_counts: dict[str, int] = {}
    for _, name in candidates:
        base = participant_name_key(name, strip_suffixes=True)
        if base:
            base_counts[base] = base_counts.get(base, 0) + 1
    for ccass_id, name in candidates:
        exact = participant_name_key(name)
        base = participant_name_key(name, strip_suffixes=True)
        if exact:
            mapping[exact] = (ccass_id, name)
        if base and base_counts.get(base) == 1:
            mapping[base] = (ccass_id, name)
    return mapping


def canonical_participant(
    name: Any,
    ccass_id: Any = "",
    directory: dict[str, tuple[str, str]] | None = None,
) -> tuple[str, str]:
    cleaned_name = clean_participant_name(name)
    cleaned_id = clean_ccass_id(ccass_id)
    directory = directory or {}
    for key in (
        participant_name_key(cleaned_name),
        participant_name_key(cleaned_name, strip_suffixes=True),
    ):
        if key and key in directory:
            mapped_id, mapped_name = directory[key]
            return cleaned_id or mapped_id, mapped_name
    return cleaned_id, cleaned_name


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


def to_int_number(value: Any) -> Optional[int]:
    number = to_number(value)
    return int(number) if number is not None else None


def percent_text(value: Any) -> str:
    text = safe_str(value)
    if not text:
        return ""
    return text if "%" in text else f"{text}%"


def _scaled_percentage(component: Any, base: Any) -> Optional[float]:
    component_number = to_number(component)
    base_number = to_number(base)
    if component_number is None or base_number is None:
        return None
    return round(component_number * base_number / 100, 6)


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


def normalized_date_text(value: Any) -> str:
    parsed = parse_date_value(value)
    return parsed.strftime("%Y-%m-%d") if parsed is not None else safe_str(value)


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
        name_col = pick_first_column(table, [["name"], ["stock name"]])
        if name_col:
            parsed.stock_name = safe_str(table.iloc[0].get(name_col))
    if not parsed.stock_name and not table.empty:
        joined = " ".join(table.head(3).fillna("").astype(str).to_numpy().ravel().tolist())
        parsed.stock_name = first_match(joined, [r"Name\s+(.+?)\s+Code", r"Issue\s+(.+?)\s+Code"])
    parsed.stock_name = re.sub(r"\s*:\s*[A-Z]\s+[A-Z]{3}\s*$", "", compact_text(parsed.stock_name)).strip()
    for raw_table in result.tables if result else []:
        listed_table = normalize_columns(raw_table)
        listed_col = pick_first_column(listed_table, [["listed"], ["listing date"]])
        if not listed_col:
            continue
        for value in listed_table[listed_col].tolist():
            normalized = normalized_date_text(value)
            if ISO_DATE_RE.fullmatch(normalized):
                parsed.listing_date = normalized
                break
        if parsed.listing_date:
            break
    if result and result.ok and parse.status == "failed":
        parse.status = "success"


def parse_holdings(
    result: FetchResult,
    parsed: ParsedCCASS,
    overrides: dict[str, int] | None,
    directory: dict[str, tuple[str, str]] | None = None,
) -> None:
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
        issued_rows = holder_table[holder_table["Type of holder"].astype(str).str.contains("Issued securities|Issued shares", case=False, na=False)]
        if not total_rows.empty:
            parsed.total_in_ccass = parsed.total_in_ccass or safe_str(total_rows.iloc[0].get("Holding"))
            parsed.total_in_ccass_pct = parsed.total_in_ccass_pct or percent_text(total_rows.iloc[0].get("Stake %", ""))
        if not outside_rows.empty:
            parsed.securities_not_in_ccass = parsed.securities_not_in_ccass or safe_str(outside_rows.iloc[0].get("Holding"))
        if not issued_rows.empty:
            parsed.issued_securities = parsed.issued_securities or safe_str(issued_rows.iloc[0].get("Holding"))

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
    output["Rank"] = table[rank_col].map(to_number) if rank_col else range(1, len(table) + 1)
    raw_names = table[participant_col]
    raw_ids = table[ccass_col] if ccass_col else pd.Series([""] * len(table), index=table.index)
    canonical = [
        canonical_participant(name, ccass_id, directory)
        for name, ccass_id in zip(raw_names.tolist(), raw_ids.tolist())
    ]
    output["Participant"] = [item[1] for item in canonical]
    output["CCASS ID"] = [item[0] for item in canonical]
    output["Holding"] = table[holding_col]
    last_change_col = pick_first_column(table, [["last change"], ["last_change"], ["latest change"]])
    output["Last change"] = table[last_change_col].map(normalized_date_text) if last_change_col else ""
    output["Stake %"] = table[stake_col]
    output["Cumulative %"] = table[cumulative_col] if cumulative_col else ""
    output = output.dropna(how="all")
    output = output[output["Participant"].astype(str).str.strip().ne("")]
    parsed.holdings_data_date = normalized_date_text(parsed.holdings_data_date)
    output["Date"] = parsed.holdings_data_date
    output["holding_shares"] = output["Holding"].map(to_int_number)
    output["shares_held"] = output["holding_shares"]
    output["last_change_date"] = output["Last change"]
    output["stake_pct_of_issued"] = output["Stake %"].map(to_number)
    output["issued_shares_at_date"] = to_int_number(parsed.issued_securities) if parsed.issued_securities else pd.NA
    output["ccass_total_at_date"] = to_int_number(parsed.total_in_ccass) if parsed.total_in_ccass else pd.NA
    output["cumulative_pct_of_issued"] = output["Cumulative %"].map(to_number)
    output["ccass_id"] = output["CCASS ID"]
    output["participant_name"] = output["Participant"]
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


def parse_changes(
    result: FetchResult,
    parsed: ParsedCCASS,
    overrides: dict[str, int] | None,
    directory: dict[str, tuple[str, str]] | None = None,
) -> None:
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
    canonical = [canonical_participant(value, directory=directory) for value in table[participant_col].tolist()]
    output["Participant"] = [item[1] for item in canonical]
    output["CCASS ID"] = [item[0] for item in canonical]
    output["Change"] = table[change_col]
    output["Change %"] = table[change_pct_col] if change_pct_col else table[change_col]
    output["Holding after"] = table[holding_after_col] if holding_after_col else ""
    output["Stake after"] = table[stake_after_col] if stake_after_col else ""
    output = output.dropna(how="all")
    output = output[output["Participant"].astype(str).str.strip().ne("")]
    parsed.changes_trading_date = normalized_date_text(parsed.changes_trading_date)
    output["Date"] = parsed.changes_trading_date
    output["change_shares"] = output["Change"].map(to_number)
    output["change_pct_of_issued"] = output["Change %"].map(to_number)
    output["holding_after_shares"] = output["Holding after"].map(to_number)
    output["stake_after_pct_of_issued"] = output["Stake after"].map(to_number)
    output["ccass_id"] = output["CCASS ID"]
    output["participant_name"] = output["Participant"]
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


def parse_big_changes(
    result: FetchResult,
    parsed: ParsedCCASS,
    overrides: dict[str, int] | None,
    directory: dict[str, tuple[str, str]] | None = None,
    limit: int | None = None,
) -> None:
    parse = SectionParse("Big Changes")
    parsed.section_parses[parse.section] = parse
    table = get_selected_table(parse.section, result, overrides, parse)
    if table.empty:
        return

    date_col = pick_first_column(table, [["date"]])
    participant_col = pick_first_column(table, [["participant"], ["name"]])
    ccass_col = pick_first_column(table, [["ccass id"], ["participant id"], ["id"]])
    shares_col = pick_first_column(table, [["change in shares"], ["shares changed"], ["share change"]])
    change_col = shares_col or pick_first_column(table, [["change"]])
    change_pct_col = pick_first_column(table, [["change %"], ["% change"], ["%"]])
    if any(col is None for col in [date_col, participant_col, change_col]):
        parse.status = "no matching table"
        parse.error = "Big Changes table parsing failed. Raw table previews are shown below."
        return

    output = pd.DataFrame()
    raw_dates = table[date_col].map(safe_str)
    output["Raw Date"] = raw_dates
    output["Date"] = raw_dates.replace("", pd.NA).ffill().map(normalized_date_text)
    raw_ids = table[ccass_col].tolist() if ccass_col else [""] * len(table)
    canonical = [
        canonical_participant(value, ccass_id, directory)
        for value, ccass_id in zip(table[participant_col].tolist(), raw_ids)
    ]
    output["Participant"] = [item[1] for item in canonical]
    output["CCASS ID"] = [item[0] for item in canonical]
    if shares_col:
        output["Change in shares"] = table[shares_col]
        output["Change %"] = table[change_pct_col] if change_pct_col else ""
    else:
        output["Change %"] = table[change_pct_col] if change_pct_col else table[change_col]
    output = output.dropna(how="all")
    output = output[output["Participant"].astype(str).str.strip().ne("")]
    output["change_pct"] = output["Change %"].map(to_number)
    output["change_pct_of_issued"] = output["change_pct"]
    output["change_pct_basis"] = "issued_shares"
    output["threshold_used"] = 0.25
    output["threshold_basis"] = "pct_of_issued_shares"
    output["row_meaning"] = (
        "Daily participant movement greater than 0.25% of issued/outstanding shares; "
        "Change % is measured against issued/outstanding shares."
    )
    output["ccass_id"] = output["CCASS ID"]
    output["participant_name"] = output["Participant"]

    issued_shares = to_number(parsed.issued_securities)
    if shares_col:
        output["change_shares"] = output["Change in shares"].map(to_number)
        output["change_shares_is_estimate"] = False
        output["change_shares_method"] = "source_reported"
    elif issued_shares is not None:
        output["change_shares"] = output["change_pct"].map(
            lambda value: int(round(issued_shares * value / 100)) if value is not None else None
        )
        output["change_shares_is_estimate"] = True
        output["change_shares_method"] = "rounded_change_pct_x_issued_shares_basis"
    else:
        output["change_shares"] = None
        output["change_shares_is_estimate"] = True
        output["change_shares_method"] = "unavailable_no_issued_shares_basis"
    output["issued_shares_basis"] = int(issued_shares) if issued_shares is not None else None
    output["issued_shares_basis_date"] = parsed.holdings_data_date or None

    output["holding_after"] = None
    output["stake_pct_of_issued"] = None
    output["stake_pct_of_ccass"] = None
    if not parsed.holdings_table.empty and parsed.holdings_data_date:
        holdings_by_id = {
            clean_ccass_id(row.get("CCASS ID") or row.get("ccass_id")): row
            for _, row in parsed.holdings_table.iterrows()
            if clean_ccass_id(row.get("CCASS ID") or row.get("ccass_id"))
        }
        total_in_ccass = to_number(parsed.total_in_ccass)
        same_date = output["Date"].astype(str).eq(parsed.holdings_data_date)
        for index in output.index[same_date]:
            holding_row = holdings_by_id.get(clean_ccass_id(output.at[index, "CCASS ID"]))
            if holding_row is None:
                continue
            holding_after = to_number(holding_row.get("Holding"))
            output.at[index, "holding_after"] = int(holding_after) if holding_after is not None else None
            output.at[index, "stake_pct_of_issued"] = to_number(holding_row.get("Stake %"))
            if holding_after is not None and total_in_ccass:
                output.at[index, "stake_pct_of_ccass"] = round(holding_after / total_in_ccass * 100, 6)

    columns = ["Date", "Participant", "CCASS ID"]
    if "Change in shares" in output.columns:
        columns.append("Change in shares")
    columns.extend(
        [
            "Change %",
            "change_pct",
            "change_pct_of_issued",
            "change_pct_basis",
            "change_shares",
            "change_shares_is_estimate",
            "change_shares_method",
            "issued_shares_basis",
            "issued_shares_basis_date",
            "holding_after",
            "stake_pct_of_issued",
            "stake_pct_of_ccass",
            "threshold_used",
            "threshold_basis",
            "row_meaning",
            "ccass_id",
            "participant_name",
        ]
    )
    parsed.big_changes_table = output[columns]

    if parsed.big_changes_table.empty:
        parse.status = "no matching table"
        parse.error = "Big Changes table parsing failed. Raw table previews are shown below."
        return

    parsed.big_changes_latest_date = latest_date_from_column(parsed.big_changes_table, "Date")
    parse.latest_date = parsed.big_changes_latest_date
    parsed.transfer_flags = detect_transfer_flags(parsed.big_changes_table)
    parsed.section_total_counts["Big Changes"] = len(parsed.big_changes_table)
    if limit is not None:
        parsed.big_changes_table = parsed.big_changes_table.head(max(0, limit)).copy()


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


def parse_concentration(
    result: FetchResult,
    parsed: ParsedCCASS,
    overrides: dict[str, int] | None,
    limit: int | None = None,
) -> None:
    parse = SectionParse("Concentration")
    parsed.section_parses[parse.section] = parse
    table = get_selected_table(parse.section, result, overrides, parse)
    if table.empty:
        return

    date_col = pick_first_column(table, [["date"]])
    top5_col = pick_first_column(table, [["top 5"], ["top5"]])
    top10_ncip_col = pick_first_column(table, [["top 10 + ncip"], ["top 10 +ncip"], ["top 10 ncip"], ["ncip"]])
    top10_table = table.drop(columns=[top10_ncip_col]) if top10_ncip_col else table
    top10_col = pick_first_column(top10_table, [["top 10 %"], ["top 10"], ["top10"]])
    stake_col = pick_first_column(table, [["stake in ccass"], ["ccass"], ["stake"]])
    if any(col is None for col in [date_col, top5_col, top10_col]):
        parse.status = "no matching table"
        parse.error = "Concentration table parsing failed. Raw table previews are shown below."
        return

    output = pd.DataFrame()
    output["Date"] = table[date_col].map(normalized_date_text)
    output["Top 5 %"] = table[top5_col]
    output["Top 10 %"] = table[top10_col]
    output["Top 10 + NCIP %"] = table[top10_ncip_col] if top10_ncip_col else ""
    output["Stake in CCASS %"] = table[stake_col] if stake_col else ""
    output = output.dropna(how="all")
    output = output[output["Date"].astype(str).str.strip().ne("")]

    value_columns = ["Top 5 %", "Top 10 %", "Top 10 + NCIP %", "Stake in CCASS %"]
    duplicate_conflict_dates: set[str] = set()
    for date, group in output.groupby("Date", sort=False, dropna=False):
        if len(group) <= 1:
            continue
        signatures = {
            tuple(safe_str(row.get(column)) for column in value_columns)
            for _, row in group.iterrows()
        }
        if len(signatures) > 1:
            duplicate_conflict_dates.add(safe_str(date))
            parsed.analysis_warnings.append(
                f"Concentration duplicate-date conflict: {date} has {len(group)} different rows; "
                "only the first source row was retained for audit and excluded from analysis."
            )
    output = output.drop_duplicates(subset=["Date"], keep="first").reset_index(drop=True)
    output["SUSPECT_DENOMINATOR"] = False
    output["exclude_from_analysis"] = False
    if duplicate_conflict_dates:
        conflict_rows = output["Date"].astype(str).isin(duplicate_conflict_dates)
        output.loc[conflict_rows, "SUSPECT_DENOMINATOR"] = True
        output.loc[conflict_rows, "exclude_from_analysis"] = True
    parsed.concentration_table = output

    if parsed.concentration_table.empty:
        parse.status = "no matching table"
        parse.error = "Concentration table parsing failed. Raw table previews are shown below."
        return

    parsed.concentration_latest_date = latest_date_from_column(parsed.concentration_table, "Date")
    parse.latest_date = parsed.concentration_latest_date
    validate_concentration(parsed)
    parsed.concentration_table["top5_pct_of_ccass"] = parsed.concentration_table["Top 5 %"].map(to_number)
    parsed.concentration_table["top10_pct_of_ccass"] = parsed.concentration_table["Top 10 %"].map(to_number)
    parsed.concentration_table["top10_plus_ncip_pct_of_ccass"] = parsed.concentration_table[
        "Top 10 + NCIP %"
    ].map(to_number)
    parsed.concentration_table["stake_pct_of_issued"] = parsed.concentration_table[
        "Stake in CCASS %"
    ].map(to_number)
    parsed.concentration_table["top5_pct_of_issued"] = parsed.concentration_table.apply(
        lambda row: _scaled_percentage(row.get("Top 5 %"), row.get("Stake in CCASS %")),
        axis=1,
    )
    parsed.concentration_table["top10_pct_of_issued"] = parsed.concentration_table.apply(
        lambda row: _scaled_percentage(row.get("Top 10 %"), row.get("Stake in CCASS %")),
        axis=1,
    )
    issued = to_int_number(parsed.issued_securities) if parsed.issued_securities else pd.NA
    ccass_total = to_int_number(parsed.total_in_ccass) if parsed.total_in_ccass else pd.NA
    parsed.concentration_table["issued_shares_at_date"] = issued
    parsed.concentration_table["ccass_total_at_date"] = ccass_total
    parsed.concentration_5day_change = calculate_concentration_5day_change(parsed.concentration_table)
    parsed.section_total_counts["Concentration"] = len(parsed.concentration_table)
    if limit is not None:
        parsed.concentration_table = parsed.concentration_table.head(max(0, limit)).copy()


def parse_price_history(
    result: FetchResult,
    parsed: ParsedCCASS,
    overrides: dict[str, int] | None,
    limit: int | None = None,
) -> None:
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
    output["Date"] = table[date_col].map(normalized_date_text)
    output["Close"] = table[close_col]
    output["Open"] = table[open_col] if open_col else ""
    output["High"] = table[high_col] if high_col else ""
    output["Low"] = table[low_col] if low_col else ""
    output["Volume"] = table[volume_col] if volume_col else ""
    output["Turnover"] = table[turnover_col] if turnover_col else ""
    output["VWAP"] = table[vwap_col] if vwap_col else ""
    price_source_col = pick_first_column(table, [["price_source"], ["price source"]])
    turnover_est_col = pick_first_column(table, [["turnover_est"], ["turnover est"]])
    vwap_est_col = pick_first_column(table, [["vwap_est"], ["vwap est"]])
    if price_source_col:
        output["price_source"] = table[price_source_col]
    if turnover_est_col:
        output["turnover_est"] = table[turnover_est_col]
    if vwap_est_col:
        output["vwap_est"] = table[vwap_est_col]
    output = output.dropna(how="all")
    output = output[output["Date"].astype(str).str.strip().ne("")]
    for column in ["Close", "Open", "High", "Low", "VWAP", "vwap_est"]:
        if column in output.columns:
            output[column] = pd.to_numeric(output[column], errors="coerce").round(3)
    for column in ["Turnover", "turnover_est"]:
        if column in output.columns:
            output[column] = pd.to_numeric(output[column], errors="coerce").round(2)
    if "vwap_est" not in output.columns and "price_source" in output.columns:
        volume = pd.to_numeric(output.get("Volume"), errors="coerce")
        turnover_est = pd.to_numeric(output.get("turnover_est"), errors="coerce")
        yahoo_source = output["price_source"].astype(str).str.lower().eq("yahoo")
        missing_vwap = pd.to_numeric(output.get("VWAP"), errors="coerce").isna()
        output["vwap_est"] = (turnover_est / volume.where(volume.ne(0))).where(yahoo_source & missing_vwap).round(3)
    add_block_trade_metrics(output, parsed)
    parsed.price_history_table = output

    if parsed.price_history_table.empty:
        parse.status = "no matching table"
        parse.error = "Price History table parsing failed. Raw table previews are shown below."
        return

    for date, group in parsed.price_history_table.groupby("Date", sort=False, dropna=False):
        closes = {
            value
            for value in (to_number(item) for item in group["Close"].tolist())
            if value is not None
        }
        if len(closes) > 1:
            parsed.analysis_warnings.append(
                f"Price History duplicate-date conflict: {date} has {len(closes)} different Close values."
            )

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
        parsed.price_source = safe_str(latest.get("price_source"))
        turnover_estimated = parsed.price_source == "yahoo"
        if not turnover_estimated and "turnover_est" in parsed.price_history_table.columns:
            estimated = pd.to_numeric(parsed.price_history_table["turnover_est"], errors="coerce")
            actual = pd.to_numeric(parsed.price_history_table.get("Turnover"), errors="coerce")
            turnover_estimated = estimated.notna().any() and actual.notna().sum() == 0
        if turnover_estimated:
            warning = "Turnover is estimated as volume \u00d7 close, not actual turnover"
            if warning not in parsed.analysis_warnings:
                parsed.analysis_warnings.append(warning)
        if parsed.price_source == "yahoo" and "vwap_est" in parsed.price_history_table.columns:
            warning = "VWAP is estimated from estimated turnover"
            if warning not in parsed.analysis_warnings:
                parsed.analysis_warnings.append(warning)
    parsed.section_total_counts["Price History"] = len(parsed.price_history_table)
    if limit is not None:
        parsed.price_history_table = parsed.price_history_table.head(max(0, limit)).copy()


def add_block_trade_metrics(output: pd.DataFrame, parsed: ParsedCCASS) -> None:
    """Add daily aggregate indicators for possible off-market block activity."""
    output["vwap_close_divergence_pct"] = None
    output["volume_pct_issued"] = None
    output["BLOCK_TRADE_SUSPECT"] = False
    output["implied_block_price_est"] = None
    output["implied_block_price_method"] = "unavailable"

    issued_shares = to_number(parsed.issued_securities)
    if issued_shares is None or issued_shares <= 0:
        return

    chronological = output.copy()
    chronological["_date"] = chronological["Date"].map(parse_date_value)
    chronological["_volume"] = pd.to_numeric(chronological.get("Volume"), errors="coerce")
    chronological = chronological.dropna(subset=["_date"]).sort_values("_date")
    prior_volumes: list[float] = []
    for index, row in chronological.iterrows():
        close = to_number(row.get("Close"))
        volume = to_number(row.get("Volume"))
        turnover = to_number(row.get("Turnover"))
        vwap = to_number(row.get("VWAP"))
        if vwap is None:
            vwap = to_number(row.get("vwap_est"))

        if close and vwap is not None:
            divergence = abs(vwap - close) / close * 100
            output.at[index, "vwap_close_divergence_pct"] = round(divergence, 4)
        else:
            divergence = None
        if volume is not None:
            output.at[index, "volume_pct_issued"] = round(volume / issued_shares * 100, 4)
        volume_pct = to_number(output.at[index, "volume_pct_issued"])
        suspect = bool(
            volume_pct is not None
            and volume_pct >= 5
            and divergence is not None
            and divergence >= 30
        )
        output.at[index, "BLOCK_TRADE_SUSPECT"] = suspect

        if suspect and volume and turnover is not None:
            normal_volume = float(pd.Series(prior_volumes[-20:]).median()) if prior_volumes else 0.0
            block_volume = volume - normal_volume
            if block_volume > 0 and close is not None:
                implied = (turnover - normal_volume * close) / block_volume
                output.at[index, "implied_block_price_est"] = round(implied, 4)
                output.at[index, "implied_block_price_method"] = "daily_ohlcv_residual"
            else:
                output.at[index, "implied_block_price_est"] = round(vwap, 4) if vwap is not None else None
                output.at[index, "implied_block_price_method"] = "daily_vwap_fallback"

            settlement_date, calendar_warning = shift_trading_date(row.get("Date"), 2)
            implied_text = output.at[index, "implied_block_price_est"]
            warning = (
                f"BLOCK_TRADE_SUSPECT: {row.get('Date')} volume {int(volume):,} shares "
                f"({volume_pct:.1f}% of issued), VWAP {vwap:.3f} differs from close {close:.3f} "
                f"by {divergence:.1f}%; implied block price estimate {implied_text}. "
                "The implied price is a daily-bar estimate; an exact block price requires "
                "tick or special-trade data. "
                f"Check T+2 Holdings Diff on {settlement_date or 'an unavailable settlement date'}."
            )
            if warning not in parsed.analysis_warnings:
                parsed.analysis_warnings.append(warning)
            if calendar_warning:
                calendar_message = f"Price History: {calendar_warning}"
                if calendar_message not in parsed.analysis_warnings:
                    parsed.analysis_warnings.append(calendar_message)

        if volume is not None and volume >= 0:
            prior_volumes.append(volume)


def calculate_concentration_5day_change(df: pd.DataFrame) -> dict[str, str]:
    if df.empty or len(df) < 2:
        return {}
    sorted_df = df.copy()
    if "exclude_from_analysis" in sorted_df.columns:
        excluded = sorted_df["exclude_from_analysis"].fillna(False).astype(str).str.lower().isin({"true", "1", "yes"})
        sorted_df = sorted_df[~excluded]
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
    for column in value_columns:
        if column in parsed.concentration_table.columns:
            parsed.concentration_table[column] = parsed.concentration_table[column].astype(object)
    for idx, row in parsed.concentration_table.iterrows():
        date = safe_str(row.get("Date")) or f"row {idx + 1}"
        row_warnings: list[str] = []
        for column in value_columns:
            value = to_number(row.get(column))
            if value is None:
                continue
            if value < 0 or value > 100:
                raw_column = f"{column} raw"
                if raw_column not in parsed.concentration_table.columns:
                    parsed.concentration_table[raw_column] = ""
                parsed.concentration_table.at[idx, raw_column] = row.get(column)
                parsed.concentration_table.at[idx, column] = "not available"
                row_warnings.append(
                    f"Abnormal concentration value withheld: {date} {column} = {row.get(column)}. "
                    "Expected range is 0-100; possible share-capital consolidation/split denominator mismatch."
                )
        top5 = to_number(parsed.concentration_table.at[idx, "Top 5 %"])
        top10 = to_number(parsed.concentration_table.at[idx, "Top 10 %"])
        top10_ncip = to_number(parsed.concentration_table.at[idx, "Top 10 + NCIP %"])
        if top5 is not None and top10 is not None and top5 > top10:
            row_warnings.append(
                f"Abnormal concentration hierarchy: {date} Top 5 % ({top5:g}) exceeds Top 10 % ({top10:g})."
            )
        if top10 is not None and top10_ncip is not None and top10 > top10_ncip:
            row_warnings.append(
                f"Abnormal concentration hierarchy: {date} Top 10 % ({top10:g}) exceeds Top 10 + NCIP % ({top10_ncip:g})."
            )
        if row_warnings:
            parsed.concentration_table.at[idx, "SUSPECT_DENOMINATOR"] = True
            parsed.concentration_table.at[idx, "exclude_from_analysis"] = True
            for warning in row_warnings:
                if warning not in parsed.analysis_warnings:
                    parsed.analysis_warnings.append(warning)


def mark_concentration_capital_change_dates(
    parsed: ParsedCCASS,
    share_changes: list[dict[str, Any]] | None,
) -> None:
    """Flag concentration rows whose denominator may straddle a capital event."""
    if parsed.concentration_table.empty or not share_changes:
        return

    events: dict[str, list[str]] = {}
    for record in share_changes:
        if not isinstance(record, dict):
            continue
        event_date = normalized_date_text(
            record.get("change_date")
            or record.get("effective_date")
            or record.get("date")
        )
        if not event_date:
            continue
        reason = safe_str(record.get("reason") or record.get("event") or "share capital change")
        events.setdefault(event_date, []).append(reason)

    if not events:
        return
    if "SUSPECT_DENOMINATOR" not in parsed.concentration_table.columns:
        parsed.concentration_table["SUSPECT_DENOMINATOR"] = False
    if "exclude_from_analysis" not in parsed.concentration_table.columns:
        parsed.concentration_table["exclude_from_analysis"] = False

    for idx, row in parsed.concentration_table.iterrows():
        row_date = normalized_date_text(row.get("Date"))
        if row_date not in events:
            continue
        parsed.concentration_table.at[idx, "SUSPECT_DENOMINATOR"] = True
        parsed.concentration_table.at[idx, "exclude_from_analysis"] = True
        reasons = "; ".join(dict.fromkeys(events[row_date]))
        warning = (
            f"SUSPECT_DENOMINATOR: Concentration percentages on {row_date} match a Share Capital "
            f"Changes effective date ({reasons}) and are excluded from concentration-change analysis."
        )
        if warning not in parsed.analysis_warnings:
            parsed.analysis_warnings.append(warning)

    parsed.concentration_5day_change = calculate_concentration_5day_change(parsed.concentration_table)

    # Keep suspect source rows visible, but do not promote them into the
    # broker-level summary when a clean concentration observation exists.
    if parsed.holdings_table.empty:
        analysis_rows = parsed.concentration_table.copy()
        excluded = analysis_rows["exclude_from_analysis"].fillna(False).astype(str).str.lower().isin(
            {"true", "1", "yes"}
        )
        analysis_rows = analysis_rows[~excluded]
        analysis_rows["_date"] = analysis_rows["Date"].map(parse_date_value)
        analysis_rows = analysis_rows.dropna(subset=["_date"]).sort_values("_date", ascending=False)
        if not analysis_rows.empty:
            latest_valid = analysis_rows.iloc[0]
            parsed.top5_cumulative_pct = percent_text(latest_valid.get("Top 5 %", ""))
            parsed.top10_cumulative_pct = percent_text(latest_valid.get("Top 10 %", ""))


def fallback_concentration_from_holdings(parsed: ParsedCCASS, result: FetchResult | None) -> None:
    if not parsed.concentration_table.empty or parsed.holdings_table.empty:
        return
    parsed.concentration_table = pd.DataFrame(
        [
            {
                "Date": parsed.holdings_data_date,
                "Top 5 %": parsed.top5_cumulative_pct,
                "Top 10 %": parsed.top10_cumulative_pct,
                "Top 10 + NCIP %": "",
                "Stake in CCASS %": parsed.total_in_ccass_pct,
            }
        ]
    )
    parsed.concentration_latest_date = parsed.holdings_data_date
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
        holdings = parsed.section_parses.get("Holdings")
        if holdings and holdings.status == "skipped":
            if "requires browser" in holdings.error.lower():
                parsed.analysis_warnings.append(
                    "Holdings not fetched in hybrid_light (requires browser); broker-level analysis unavailable."
                )
        else:
            parsed.analysis_warnings.append(
                "Concentration succeeded, but Holdings failed. Full broker-level analysis is incomplete."
            )
    if not parsed.big_changes_table.empty and parsed.changes_table.empty:
        changes = parsed.section_parses.get("Changes")
        if changes and changes.status == "skipped":
            if "requires browser" in changes.error.lower():
                parsed.analysis_warnings.append(
                    "Daily Changes not fetched in hybrid_light (requires browser); recent daily movement cannot be confirmed."
                )
        else:
            parsed.analysis_warnings.append(
                "Big Changes succeeded, but daily Changes failed. Recent daily movement cannot be confirmed."
            )


def _annotate_frame_dates(
    parsed: ParsedCCASS,
    section: str,
    frame: pd.DataFrame,
    default_date: str = "",
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    records, warnings = annotate_records(frame.to_dict(orient="records"), section, default_date=default_date)
    for warning in warnings:
        if warning not in parsed.analysis_warnings:
            parsed.analysis_warnings.append(warning)
    return pd.DataFrame(records)


def apply_date_semantics(parsed: ParsedCCASS) -> None:
    """Attach explicit source/implied dates once for every output path."""
    parsed.date_basis_by_section = {
        section.lower().replace(" ", "_"): basis
        for section, basis in SECTION_DATE_BASIS.items()
        if section in {"Holdings", "Changes", "Big Changes", "Concentration"}
    }
    parsed.settlement_note = SETTLEMENT_NOTE

    parsed.holdings_table = _annotate_frame_dates(
        parsed, "Holdings", parsed.holdings_table, parsed.holdings_data_date
    )
    parsed.changes_table = _annotate_frame_dates(
        parsed, "Changes", parsed.changes_table, parsed.changes_trading_date
    )
    parsed.big_changes_table = _annotate_frame_dates(
        parsed, "Big Changes", parsed.big_changes_table, parsed.big_changes_latest_date
    )
    parsed.concentration_table = _annotate_frame_dates(
        parsed, "Concentration", parsed.concentration_table, parsed.concentration_latest_date
    )
    parsed.price_history_table = _annotate_frame_dates(
        parsed, "Price History", parsed.price_history_table, parsed.price_history_latest_date
    )

    # Concentration is independently sufficient for Top 5 / Top 10. Do not
    # blank these values merely because broker-level Holdings failed.
    if not parsed.concentration_table.empty:
        analysis_rows = parsed.concentration_table.copy()
        if "exclude_from_analysis" in analysis_rows.columns:
            excluded = analysis_rows["exclude_from_analysis"].fillna(False).astype(str).str.lower().isin(
                {"true", "1", "yes"}
            )
            analysis_rows = analysis_rows[~excluded]
        latest_concentration = analysis_rows.sort_values(
            "ccass_date", ascending=False, na_position="last"
        ).iloc[0] if not analysis_rows.empty else None
        if latest_concentration is not None:
            parsed.top5_cumulative_pct = parsed.top5_cumulative_pct or percent_text(
                latest_concentration.get("Top 5 %", "")
            )
            parsed.top10_cumulative_pct = parsed.top10_cumulative_pct or percent_text(
                latest_concentration.get("Top 10 %", "")
            )

    derivations = {
        "Holdings": derive_dates(parsed.holdings_data_date, SECTION_DATE_BASIS["Holdings"]),
        "Changes": derive_dates(parsed.changes_trading_date, SECTION_DATE_BASIS["Changes"]),
        "Big Changes": derive_dates(parsed.big_changes_latest_date, SECTION_DATE_BASIS["Big Changes"]),
        "Concentration": derive_dates(
            parsed.concentration_latest_date, SECTION_DATE_BASIS["Concentration"]
        ),
    }
    parsed.holdings_implied_trade_date = derivations["Holdings"].implied_trade_date
    parsed.changes_implied_trade_date = derivations["Changes"].implied_trade_date
    parsed.big_changes_implied_trade_date = derivations["Big Changes"].implied_trade_date
    parsed.concentration_implied_trade_date = derivations["Concentration"].implied_trade_date

    for section, derivation in derivations.items():
        if derivation.ccass_date and derivation.warning:
            warning = f"{section}: {derivation.warning}"
            if warning not in parsed.analysis_warnings:
                parsed.analysis_warnings.append(warning)

    implied_trade_dates = [
        value
        for value in (
            parsed.holdings_implied_trade_date,
            parsed.concentration_implied_trade_date,
            parsed.changes_implied_trade_date,
            parsed.big_changes_implied_trade_date,
        )
        if value
    ]
    parsed.data_as_of_trading_date = max(
        implied_trade_dates,
        default=unavailable_data_as_of(
            "no dated Holdings, Concentration, Changes or Big Changes row parsed"
        ),
    )


def validate_date_sanity(parsed: ParsedCCASS) -> None:
    """Fail loudly when a parsed row date cannot be trusted.

    In particular, Webb-site Big Changes uses YY-MM-DD. A generic date parser can
    silently reinterpret values such as 16-01-13 as 2013-01-16, so every parsed
    row date is required to be ISO and bounded by listing/fetch dates.
    """
    lower = parse_date_value(parsed.listing_date)
    upper = parse_date_value(parsed.fetched_time)
    lower_date = lower.date() if lower is not None else None
    upper_date = upper.date() if upper is not None else date.today()
    frames = {
        "Holdings": parsed.holdings_table,
        "Changes": parsed.changes_table,
        "Big Changes": parsed.big_changes_table,
        "Concentration": parsed.concentration_table,
        "Price History": parsed.price_history_table,
    }
    errors: list[str] = []
    for section, frame in frames.items():
        if frame is None or frame.empty:
            continue
        date_columns = [
            column
            for column in frame.columns
            if str(column) == "Date"
            or str(column).lower() in {"ccass_date", "issued_shares_basis_date"}
        ]
        for column in date_columns:
            for row_number, value in enumerate(frame[column].tolist(), start=1):
                text = safe_str(value)
                if not text:
                    continue
                if not ISO_DATE_RE.fullmatch(text):
                    errors.append(
                        f"{section} row {row_number} {column}={text!r} is not ISO YYYY-MM-DD"
                    )
                    continue
                try:
                    value_date = date.fromisoformat(text)
                except ValueError:
                    errors.append(f"{section} row {row_number} {column}={text!r} is not a valid date")
                    continue
                if lower_date is not None and value_date < lower_date:
                    errors.append(
                        f"{section} row {row_number} {column}={text} is earlier than listing date "
                        f"{lower_date.isoformat()}"
                    )
                if value_date > upper_date:
                    errors.append(
                        f"{section} row {row_number} {column}={text} is later than fetched date "
                        f"{upper_date.isoformat()}"
                    )
    if errors:
        message = "DATE_SANITY_ERROR: " + "; ".join(errors[:8])
        if len(errors) > 8:
            message += f"; and {len(errors) - 8} more invalid date value(s)"
        if message not in parsed.analysis_warnings:
            parsed.analysis_warnings.append(message)
        raise DateSanityError(message)


def validate_concentration_identity(parsed: ParsedCCASS, tolerance_pp: float = 0.15) -> None:
    """Cross-check the two concentration denominators against Holdings."""
    if parsed.holdings_table.empty or parsed.concentration_table.empty:
        return
    if not parsed.holdings_data_date:
        return
    matching = parsed.concentration_table[
        parsed.concentration_table["Date"].astype(str).eq(parsed.holdings_data_date)
    ]
    if matching.empty:
        return
    row = matching.iloc[0]
    if str(row.get("exclude_from_analysis", "")).strip().lower() in {"true", "1", "yes"}:
        return
    top5_ccass = to_number(row.get("Top 5 %"))
    stake_in_ccass = to_number(row.get("Stake in CCASS %"))
    holdings_top5 = sum(
        value
        for value in parsed.holdings_table.head(5)["Stake %"].map(to_number).tolist()
        if value is not None
    )
    if top5_ccass is None or stake_in_ccass is None or not holdings_top5:
        return
    expected = top5_ccass * stake_in_ccass / 100
    difference = abs(expected - holdings_top5)
    parsed.concentration_table.loc[matching.index, "top5_identity_expected_pct_of_issued"] = round(expected, 6)
    parsed.concentration_table.loc[matching.index, "top5_identity_holdings_pct_of_issued"] = round(
        holdings_top5, 6
    )
    parsed.concentration_table.loc[matching.index, "top5_identity_difference_pp"] = round(difference, 6)
    parsed.concentration_table.loc[matching.index, "top5_identity_check"] = (
        "pass" if difference <= tolerance_pp else "warning"
    )
    if difference > tolerance_pp:
        warning = (
            f"Concentration/Holdings denominator check failed on {parsed.holdings_data_date}: "
            f"Top 5 % of CCASS x Stake in CCASS % = {expected:.4f}% of issued shares, "
            f"but Holdings top-five stake sums to {holdings_top5:.4f}% "
            f"(difference {difference:.4f} percentage points)."
        )
        if warning not in parsed.analysis_warnings:
            parsed.analysis_warnings.append(warning)


def update_section_asof(parsed: ParsedCCASS) -> None:
    frames = {
        "Company / orgdata": parsed.company_table,
        "Holdings": parsed.holdings_table,
        "Changes": parsed.changes_table,
        "Big Changes": parsed.big_changes_table,
        "Concentration": parsed.concentration_table,
        "Price History": parsed.price_history_table,
    }
    latest_values = {
        "Company / orgdata": "",
        "Holdings": parsed.holdings_data_date,
        "Changes": parsed.changes_trading_date,
        "Big Changes": parsed.big_changes_latest_date,
        "Concentration": parsed.concentration_latest_date,
        "Price History": parsed.price_history_latest_date,
    }
    parsed.section_asof = {}
    for section, frame in frames.items():
        parse = parsed.section_parses.get(section)
        parsed.section_asof[section] = {
            "latest_date": latest_values.get(section, ""),
            "row_count": int(len(frame)) if frame is not None else 0,
            "date_basis": SECTION_DATE_BASIS.get(section, "not_applicable"),
            "status": parse.status if parse else ("success" if frame is not None and not frame.empty else "failed"),
            "lag_trading_days": None,
        }

    dated = {
        section: value
        for section, value in latest_values.items()
        if ISO_DATE_RE.fullmatch(safe_str(value))
    }
    if not dated:
        return
    reference_date = max(dated.values())
    priority = ("Concentration", "Holdings", "Changes", "Price History", "Big Changes")
    reference_section = next(
        (section for section in priority if dated.get(section) == reference_date),
        next(section for section, value in dated.items() if value == reference_date),
    )
    for section, section_date in dated.items():
        sessions, calendar_warning = trading_sessions_between(section_date, reference_date)
        if calendar_warning and not sessions:
            warning = f"{section} freshness check unavailable: {calendar_warning}"
            if warning not in parsed.analysis_warnings:
                parsed.analysis_warnings.append(warning)
            continue
        lag = max(len(sessions) - 1, 0)
        parsed.section_asof[section]["lag_trading_days"] = lag
        parsed.section_asof[section]["reference_section"] = reference_section
        parsed.section_asof[section]["reference_date"] = reference_date
        if lag <= 3:
            continue
        uncovered_from = sessions[1] if len(sessions) > 1 else section_date
        warning = (
            f"{section} coverage ends {section_date}; it is {lag} XHKG trading sessions behind "
            f"{reference_section} ({reference_date}). Movements from {uncovered_from} onward are not covered."
        )
        if warning not in parsed.analysis_warnings:
            parsed.analysis_warnings.append(warning)


def parse_results(
    issue_id: str,
    results: dict[str, FetchResult],
    stock_code: str = "",
    id_lookup_method: str = "",
    id_lookup_status: str = "",
    selected_indices: dict[str, int] | None = None,
    source_metadata: dict[str, Any] | None = None,
    section_limits: dict[str, int] | None = None,
) -> ParsedCCASS:
    parsed = ParsedCCASS(
        issue_id=issue_id,
        stock_code=clean_stock_code(stock_code),
        id_lookup_method=id_lookup_method,
        id_lookup_status=id_lookup_status,
    )
    fetched_times = [item.fetched_time for item in results.values() if item.fetched_time]
    parsed.fetched_time = max(fetched_times) if fetched_times else ""
    if source_metadata:
        parsed.source = safe_str(source_metadata.get("source"))
        parsed.mirror_status = safe_str(source_metadata.get("mirror_status"))
        parsed.mirror_base_url = safe_str(source_metadata.get("mirror_base_url"))
        try:
            parsed.history_depth_days = int(source_metadata.get("history_depth_days") or 0)
        except (TypeError, ValueError):
            parsed.history_depth_days = 0
        parsed.db_restored_from_backup = bool(source_metadata.get("db_restored_from_backup", False))
        parsed.db_snapshot_id = safe_str(source_metadata.get("db_snapshot_id"))
        parsed.db_updated_at = safe_str(source_metadata.get("db_updated_at"))
        parsed.db_latest_snapshot_date = safe_str(source_metadata.get("db_latest_snapshot_date"))
        parsed.db_latest_price_date = safe_str(source_metadata.get("db_latest_price_date"))
        try:
            parsed.db_snapshot_rows = int(source_metadata.get("db_snapshot_rows") or 0)
            parsed.db_price_rows = int(source_metadata.get("db_price_rows") or 0)
        except (TypeError, ValueError):
            parsed.db_snapshot_rows = 0
            parsed.db_price_rows = 0

    directory = participant_directory_map(results.get("Participants"))
    section_limits = section_limits or {}
    if results.get("Company / orgdata"):
        parse_company(results["Company / orgdata"], parsed, selected_indices)
    if results.get("Holdings"):
        if results["Holdings"].ok:
            parse_holdings(results["Holdings"], parsed, selected_indices, directory)
        else:
            status = "skipped" if getattr(results["Holdings"], "skipped", False) else "failed"
            parsed.section_parses["Holdings"] = SectionParse("Holdings", status=status, error=results["Holdings"].error_message)
    if results.get("Changes"):
        if results["Changes"].ok:
            parse_changes(results["Changes"], parsed, selected_indices, directory)
        else:
            status = "skipped" if getattr(results["Changes"], "skipped", False) else "failed"
            parsed.section_parses["Changes"] = SectionParse("Changes", status=status, error=results["Changes"].error_message)
    if results.get("Big Changes"):
        if results["Big Changes"].ok:
            parse_big_changes(
                results["Big Changes"],
                parsed,
                selected_indices,
                directory,
                section_limits.get("Big Changes"),
            )
        else:
            status = "skipped" if getattr(results["Big Changes"], "skipped", False) else "failed"
            parsed.section_parses["Big Changes"] = SectionParse("Big Changes", status=status, error=results["Big Changes"].error_message)
    if results.get("Concentration"):
        if results["Concentration"].ok:
            parse_concentration(
                results["Concentration"],
                parsed,
                selected_indices,
                section_limits.get("Concentration"),
            )
        else:
            status = "skipped" if getattr(results["Concentration"], "skipped", False) else "failed"
            parsed.section_parses["Concentration"] = SectionParse("Concentration", status=status, error=results["Concentration"].error_message)
    if results.get("Price History"):
        if results["Price History"].ok:
            parse_price_history(
                results["Price History"],
                parsed,
                selected_indices,
                section_limits.get("Price History"),
            )
        else:
            status = "skipped" if getattr(results["Price History"], "skipped", False) else "failed"
            parsed.section_parses["Price History"] = SectionParse("Price History", status=status, error=results["Price History"].error_message)

    for section, section_parse in parsed.section_parses.items():
        result = results.get(section)
        if not result or not result.ok or section_parse.status != "no matching table":
            continue
        message = section_parse.error or f"{section} table parsing failed. Raw table previews are shown below."
        if not message.startswith("PARSE_MISS:"):
            message = f"PARSE_MISS: {message}"
        section_parse.error = message
        result.error_type = "PARSE_MISS"
        result.error_message = message

    fallback_concentration_from_holdings(parsed, results.get("Concentration"))
    validate_concentration_identity(parsed)
    apply_date_semantics(parsed)
    validate_date_sanity(parsed)
    update_section_asof(parsed)
    add_cross_section_warnings(parsed)
    assess_completeness(parsed)
    return parsed


def assess_completeness(parsed: ParsedCCASS) -> None:
    """Classify output completeness so failed critical data cannot look complete."""
    # Price History is optional in hybrid_light and is not required for CCASS
    # completeness. Browser-required skips still make the result partial or
    # degraded, but they are not reported as fetch failures.
    critical = ("Holdings", "Concentration")
    degraded = ("Changes", "Big Changes")
    failed_critical = []
    skipped_critical = []
    for section in critical:
        result = parsed.section_parses.get(section)
        frame = {
            "Holdings": parsed.holdings_table,
            "Concentration": parsed.concentration_table,
        }[section]
        if result is not None and result.status == "skipped":
            skipped_critical.append(section)
        elif result is None or result.status not in {"success", "manually selected"} or frame.empty:
            failed_critical.append(section)
    parsed.critical_sections_failed = failed_critical
    failed_degraded = []
    skipped_degraded = []
    for section in degraded:
        result = parsed.section_parses.get(section)
        if result is not None and result.status == "skipped":
            skipped_degraded.append(section)
        elif result is None or result.status not in {"success", "manually selected"}:
            failed_degraded.append(section)
    if failed_critical:
        parsed.completeness_status = "partial"
        message = "Critical sections failed: " + ", ".join(failed_critical) + ". Primary export must be treated as partial."
    elif skipped_critical:
        parsed.completeness_status = "partial"
        message = ""
    elif failed_degraded:
        parsed.completeness_status = "degraded"
        message = "Degraded sections failed: " + ", ".join(failed_degraded) + "."
    elif skipped_degraded:
        parsed.completeness_status = "degraded"
        message = ""
    else:
        parsed.completeness_status = "complete"
        message = ""
    if message and message not in parsed.analysis_warnings:
        parsed.analysis_warnings.append(message)


def build_fetch_summary(parsed: ParsedCCASS, results: dict[str, FetchResult]) -> pd.DataFrame:
    rows = []
    resolver_result = results.get("Company / orgdata")
    resolver_error = ""
    if resolver_result and not resolver_result.ok:
        resolver_error = resolver_result.error_message or resolver_result.error_type or "resolver failed"
    elif not parsed.issue_id:
        resolver_error = "no resolver result was retained"
    for section in SECTIONS:
        result = results.get(section)
        parse = parsed.section_parses.get(section, SectionParse(section))
        status = parse.status
        if result and getattr(result, "skipped", False):
            status = "skipped"
        elif result and not result.ok:
            status = "failed"
        error = parse.error or (result.error_message if result and not result.ok else "")
        if error and result and not result.ok and result.error_type and result.error_type not in error:
            error = f"{result.error_type}: {error}"
        attempted_sources = getattr(result, "attempted_sources", []) if result else []
        if not error and attempted_sources:
            failures = [
                f"{item.get('error_type') or 'error'}: {item.get('error_message') or 'remote refresh failed'}"
                for item in attempted_sources
                if not item.get("ok")
            ]
            if failures:
                error = "Remote refresh failed; local data retained: " + "; ".join(failures)
        if not error and status in {"failed", "no matching table", "partial success"}:
            error = f"Issue ID unresolved: {resolver_error}" if resolver_error else "No fetch result was retained for this section."
        rows.append(
            {
                "Section": section,
                "URL": result.url if result else "",
                "Fetch method": result.method if result else "",
                "Status": status,
                "Tables found": len(result.tables) if result else 0,
                "Selected table index": parse.selected_table_index if parse.selected_table_index is not None else "",
                "Latest date / data date": parse.latest_date,
                "Error": error,
            }
        )
    return pd.DataFrame(rows)
