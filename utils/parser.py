from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd
from bs4 import BeautifulSoup

from .date_semantics import (
    SECTION_DATE_BASIS,
    SETTLEMENT_NOTE,
    annotate_records,
    derive_dates,
    unavailable_data_as_of,
)
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
    source: str = ""
    mirror_status: str = ""
    mirror_base_url: str = ""
    history_depth_days: int = 0
    db_restored_from_backup: bool = False
    fetched_time: str = ""
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
    settlement_note: str = SETTLEMENT_NOTE
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
        name_col = pick_first_column(table, [["name"], ["stock name"]])
        if name_col:
            parsed.stock_name = safe_str(table.iloc[0].get(name_col))
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
     ãM}¶‰žËkºwµç}Í•}½°€ôÁ¥­}™¥ÉÍÑ}½±Õµ¸¡Ñ…‰±”°ml‰±½Í”‰t°l‰ÁÉ¥”‰ut¤(€€€Ù½±Õµ•}½°€ôÁ¥­}™¥ÉÍÑ}½±Õµ¸¡Ñ…‰±”°ml‰Ù½±Õµ”‰t°l‰Ù½°‰ut¤(€€€ÑÕÉ¹½Ù•É}½°€ôÁ¥­}™¥ÉÍÑ}½±Õµ¸¡Ñ…‰±”°ml‰ÑÕÉ¹½Ù•È‰t°l‰Ù…±Õ”‰t°l‰…µ½Õ¹Ð‰ut¤(€€€ÙÝ…Á}½°€ôÁ¥­}™¥ÉÍÑ}½±Õµ¸¡Ñ…‰±”°ml‰ÙÝ…À‰t°l‰…Ù•É…”ÁÉ¥”‰t°l‰…ÙœÁÉ¥”‰ut¤(€€€¡¥¡}½°€ôÁ¥­}™¥ÉÍÑ}½±Õµ¸¡Ñ…‰±”°ml‰¡¥ ‰ut¤(€€€±½Ý}½°€ôÁ¥­}™¥ÉÍÑ}½±Õµ¸¡Ñ…‰±”°ml‰±½Ü‰ut¤(€€€½Á•¹}½°€ôÁ¥­}™¥ÉÍÑ}½±Õµ¸¡Ñ…‰±”°ml‰½Á•¸‰ut¤((€€€¥˜…¹ä¡½°¥Ì9½¹”™½È½°¥¸m‘…Ñ•}½°°±½Í•}½±t¤è(€€€€€€€Á…ÉÍ”¹ÍÑ…ÑÕÌ€ô€‰¹¼µ…Ñ¡¥¹œÑ…‰±”ˆ(€€€€€€€Á…ÉÍ”¹•ÉÉ½È€ô€‰AÉ¥”!¥ÍÑ½ÉäÑ…‰±”Á…ÉÍ¥¹œ™…¥±•¸I…ÜÑ…‰±”ÁÉ•Ù¥•ÝÌ…É”Í¡½Ý¸‰•±½Ü¸ˆ(€€€€€€€É•ÑÕÉ¸((€€€½ÕÑÁÕÐ€ôÁ¹…Ñ…É…µ” ¤(€€€½ÕÑÁÕÑl‰…Ñ”‰t€ôÑ…‰±•m‘…Ñ•}½±t(€€€½ÕÑÁÕÑl‰±½Í”‰t€ôÑ…‰±•m±½Í•}½±t(€€€½ÕÑÁÕÑl‰=Á•¸‰t€ôÑ…‰±•m½Á•¹}½±t¥˜½Á•¹}½°•±Í”€ˆˆ(€€€½ÕÑÁÕÑl‰!¥ ‰t€ôÑ…‰±•m¡¥¡}½±t¥˜¡¥¡}½°•±Í”€ˆˆ(€€€½ÕÑÁÕÑl‰1½Ü‰t€ôÑ…‰±•m±½Ý}½±t¥˜±½Ý}½°•±Í”€ˆˆ(€€€½ÕÑÁÕÑl‰Y½±Õµ”‰t€ôÑ…‰±•mÙ½±Õµ•}½±t¥˜Ù½±Õµ•}½°•±Í”€ˆˆ(€€€½ÕÑÁÕÑl‰QÕÉ¹½Ù•È‰t€ôÑ…‰±•mÑÕÉ¹½Ù•É}½±t¥˜ÑÕÉ¹½Ù•É}½°•±Í”€ˆˆ(€€€½ÕÑÁÕÑl‰Y]@‰t€ôÑ…‰±•mÙÝ…Á}½±t¥˜ÙÝ…Á}½°•±Í”€ˆˆ(€€€ÁÉ¥•}Í½ÕÉ•}½°€ôÁ¥­}™¥ÉÍÑ}½±Õµ¸¡Ñ…‰±”°ml‰ÁÉ¥•}Í½ÕÉ”‰t°l‰ÁÉ¥”Í½ÕÉ”‰ut¤(€€€ÑÕÉ¹½Ù•É}•ÍÑ}½°€ôÁ¥­}™¥ÉÍÑ}½±Õµ¸¡Ñ…‰±”°ml‰ÑÕÉ¹½Ù•É}•ÍÐ‰t°l‰ÑÕÉ¹½Ù•È•ÍÐ‰ut¤(€€€ÙÝ…Á}•ÍÑ}½°€ôÁ¥­}™¥ÉÍÑ}½±Õµ¸¡Ñ…‰±”°ml‰ÙÝ…Á}•ÍÐ‰t°l‰ÙÝ…À•ÍÐ‰ut¤(€€€¥˜ÁÉ¥•}Í½ÕÉ•}½°è(€€€€€€€½ÕÑÁÕÑl‰ÁÉ¥•}Í½ÕÉ”‰t€ôÑ…‰±•mÁÉ¥•}Í½ÕÉ•}½±t(€€€¥˜ÑÕÉ¹½Ù•É}•ÍÑ}½°è(€€€€€€€½ÕÑÁÕÑl‰ÑÕÉ¹½Ù•É}•ÍÐ‰t€ôÑ…‰±•mÑÕÉ¹½Ù•É}•ÍÑ}½±t(€€€¥˜ÙÝ…Á}•ÍÑ}½°è(€€€€€€€½ÕÑÁÕÑl‰ÙÝ…Á}•ÍÐ‰t€ôÑ…‰±•mÙÝ…Á}•ÍÑ}½±t(€€€½ÕÑÁÕÐ€ô½ÕÑÁÕÐ¹‘É½Á¹„¡¡½Üô‰…±°ˆ¤(€€€½ÕÑÁÕÐ€ô½ÕÑÁÕÑm½ÕÑÁÕÑl‰…Ñ”‰t¹…ÍÑåÁ”¡ÍÑÈ¤¹ÍÑÈ¹ÍÑÉ¥À ¤¹¹” ˆˆ¥t(€€€™½È½±Õµ¸¥¸l‰±½Í”ˆ°€‰=Á•¸ˆ°€‰!¥ ˆ°€‰1½Üˆ°€‰Y]@ˆ°€‰ÙÝ…Á}•ÍÐ‰tè(€€€€€€€¥˜½±Õµ¸¥¸½ÕÑÁÕÐ¹½±Õµ¹Ìè(€€€€€€€€€€€½ÕÑÁÕÑm½±Õµ¹t€ôÁ¹Ñ½}¹Õµ•É¥Œ¡½ÕÑÁÕÑm½±Õµ¹t°•ÉÉ½ÉÌô‰½•É”ˆ¤¹É½Õ¹ Ì¤(€€€™½È½±Õµ¸¥¸l‰QÕÉ¹½Ù•Èˆ°€‰ÑÕÉ¹½Ù•É}•ÍÐ‰tè(€€€€€€€¥˜½±Õµ¸¥¸½ÕÑÁÕÐ¹½±Õµ¹Ìè(€€€€€€€€€€€½ÕÑÁÕÑm½±Õµ¹t€ôÁ¹Ñ½}¹Õµ•É¥Œ¡½ÕÑÁÕÑm½±Õµ¹t°•ÉÉ½ÉÌô‰½•É”ˆ¤¹É½Õ¹ È¤(€€€¥˜€‰ÙÝ…Á}•ÍÐˆ¹½Ð¥¸½ÕÑÁÕÐ¹½±Õµ¹Ì…¹€‰ÁÉ¥•}Í½ÕÉ”ˆ¥¸½ÕÑÁÕÐ¹½±Õµ¹Ìè(€€€€€€€Ù½±Õµ”€ôÁ¹Ñ½}¹Õµ•É¥Œ¡½ÕÑÁÕÐ¹•Ð ‰Y½±Õµ”ˆ¤°•ÉÉ½ÉÌô‰½•É”ˆ¤(€€€€€€€ÑÕÉ¹½Ù•É}•ÍÐ€ôÁ¹Ñ½}¹Õµ•É¥Œ¡½ÕÑÁÕÐ¹•Ð ‰ÑÕÉ¹½Ù•É}•ÍÐˆ¤°•ÉÉ½ÉÌô‰½•É”ˆ¤(€€€€€€€å…¡½½}Í½ÕÉ”€ô½ÕÑÁÕÑl‰ÁÉ¥•}Í½ÕÉ”‰t¹…ÍÑåÁ”¡ÍÑÈ¤¹ÍÑÈ¹±½Ý•È ¤¹•Ä ‰å…¡½¼ˆ¤(€€€€€€€µ¥ÍÍ¥¹}ÙÝ…À€ôÁ¹Ñ½}¹Õµ•É¥Œ¡½ÕÑÁÕÐ¹•Ð ‰Y]@ˆ¤°•ÉÉ½ÉÌô‰½•É”ˆ¤¹¥Í¹„ ¤(€€€€€€€½ÕÑÁÕÑl‰ÙÝ…Á}•ÍÐ‰t€ô€¡ÑÕÉ¹½Ù•É}•ÍÐ€¼Ù½±Õµ”¹Ý¡•É”¡Ù½±Õµ”¹¹” À¤¤¤¹Ý¡•É”¡å…¡½½}Í½ÕÉ”€˜µ¥ÍÍ¥¹}ÙÝ…À¤¹É½Õ¹ Ì¤(€€€Á…ÉÍ•¹ÁÉ¥•}¡¥ÍÑ½Éå}Ñ…‰±”€ô½ÕÑÁÕÐ((€€€¥˜Á…ÉÍ•¹ÁÉ¥•}¡¥ÍÑ½Éå}Ñ…‰±”¹•µÁÑäè(€€€€€€€Á…ÉÍ”¹ÍÑ…ÑÕÌ€ô€‰¹¼µ…Ñ¡¥¹œÑ…‰±”ˆ(€€€€€€€Á…ÉÍ”¹•ÉÉ½È€ô€‰AÉ¥”!¥ÍÑ½ÉäÑ…‰±”Á…ÉÍ¥¹œ™…¥±•¸I…ÜÑ…‰±”ÁÉ•Ù¥•ÝÌ…É”Í¡½Ý¸‰•±½Ü¸ˆ(€€€€€€€É•ÑÕÉ¸((€€€Á…ÉÍ•¹ÁÉ¥•}¡¥ÍÑ½Éå}±…Ñ•ÍÑ}‘…Ñ”€ô±…Ñ•ÍÑ}‘…Ñ•}™É½µ}½±Õµ¸¡Á…ÉÍ•¹ÁÉ¥•}¡¥ÍÑ½Éå}Ñ…‰±”°€‰…Ñ”ˆ¤(€€€Á…ÉÍ”¹±…Ñ•ÍÑ}‘…Ñ”€ôÁ…ÉÍ•¹ÁÉ¥•}¡¥ÍÑ½Éå}±…Ñ•ÍÑ}‘…Ñ”(€€€Í½ÉÑ•‘}‘˜€ôÁ…ÉÍ•¹ÁÉ¥•}¡¥ÍÑ½Éå}Ñ…‰±”¹½Áä ¤(€€€Í½ÉÑ•‘}‘™l‰}‘…Ñ”‰t€ôÍ½ÉÑ•‘}‘™l‰…Ñ”‰t¹µ…À¡Á…ÉÍ•}‘…Ñ•}Ù…±Õ”¤(€€€Í½ÉÑ•‘}‘˜€ôÍ½ÉÑ•‘}‘˜¹‘É½Á¹„¡ÍÕ‰Í•Ðõl‰}‘…Ñ”‰t¤¹Í½ÉÑ}Ù…±Õ•Ì ‰}‘…Ñ”ˆ°…Í•¹‘¥¹œõ…±Í”¤(€€€¥˜¹½ÐÍ½ÉÑ•‘}‘˜¹•µÁÑäè(€€€€€€€±…Ñ•ÍÐ€ôÍ½ÉÑ•‘}‘˜¹¥±½lÁt(€€€€€€€Á…ÉÍ•¹±…Ñ•ÍÑ}ÁÉ¥”€ôÍ…™•}ÍÑÈ¡±…Ñ•ÍÐ¹•Ð ‰±½Í”ˆ¤¤(€€€€€€€Á…ÉÍ•¹±…Ñ•ÍÑ}ÁÉ¥•}Ù½±Õµ”€ôÍ…™•}ÍÑÈ¡±…Ñ•ÍÐ¹•Ð ‰Y½±Õµ”ˆ¤¤(€€€€€€€Á…ÉÍ•¹±…Ñ•ÍÑ}ÁÉ¥•}ÑÕÉ¹½Ù•È€ôÍ…™•}ÍÑÈ¡±…Ñ•ÍÐ¹•Ð ‰QÕÉ¹½Ù•Èˆ¤¤(€€€€€€€Á…ÉÍ•¹±…Ñ•ÍÑ}ÁÉ¥•}ÙÝ…À€ôÍ…™•}ÍÑÈ¡±…Ñ•ÍÐ¹•Ð ‰Y]@ˆ¤¤(€€€€€€€Á…ÉÍ•¹ÁÉ¥•}Í½ÕÉ”€ôÍ…™•}ÍÑÈ¡±…Ñ•ÍÐ¹•Ð ‰ÁÉ¥•}Í½ÕÉ”ˆ¤¤(€€€€€€€¥˜Á…ÉÍ•¹ÁÉ¥•}Í½ÕÉ”€ôô€‰å…¡½¼ˆ½È€‰ÑÕÉ¹½Ù•É}•ÍÐˆ¥¸Á…ÉÍ•¹ÁÉ¥•}¡¥ÍÑ½Éå}Ñ…‰±”¹½±Õµ¹Ìè(€€€€€€€€€€€Ý…É¹¥¹œ€ô€‰QÕÉ¹½Ù•È¥Ì•ÍÑ¥µ…Ñ•…ÌÙ½±Õµ”qÔÀÁÜ±½Í”°¹½Ð…ÑÕ…°ÑÕÉ¹½Ù•Èˆ(€€€€€€€€€€€¥˜Ý…É¹¥¹œ¹½Ð¥¸Á…ÉÍ•¹…¹…±åÍ¥Í}Ý…É¹¥¹Ìè(€€€€€€€€€€€€€€€Á…ÉÍ•¹…¹…±åÍ¥Í}Ý…É¹¥¹Ì¹…ÁÁ•¹¡Ý…É¹¥¹œ¤(€€€€€€€¥˜Á…ÉÍ•¹ÁÉ¥•}Í½ÕÉ”€ôô€‰å…¡½¼ˆ…¹€‰ÙÝ…Á}•ÍÐˆ¥¸Á…ÉÍ•¹ÁÉ¥•}¡¥ÍÑ½Éå}Ñ…‰±”¹½±Õµ¹Ìè(€€€€€€€€€€€Ý…É¹¥¹œ€ô€‰Y]@¥Ì•ÍÑ¥µ…Ñ•™É½´•ÍÑ¥µ…Ñ•ÑÕÉ¹½Ù•Èˆ(€€€€€€€€€€€¥˜Ý…É¹¥¹œ¹½Ð¥¸Á…ÉÍ•¹…¹…±åÍ¥Í}Ý…É¹¥¹Ìè(€€€€€€€€€€€€€€€Á…ÉÍ•¹…¹…±åÍ¥Í}Ý…É¹¥¹Ì¹…ÁÁ•¹¡Ý…É¹¥¹œ¤(()‘•˜…±Õ±…Ñ•}½¹•¹ÑÉ…Ñ¥½¹|Õ‘…å}¡…¹”¡‘˜èÁ¹…Ñ…É…µ”¤€´ø‘¥ÑmÍÑÈ°ÍÑÉtè(€€€¥˜‘˜¹•µÁÑä½È±•¸¡‘˜¤€ð€Èè(€€€€€€€É•ÑÕÉ¸íô(€€€Í½ÉÑ•‘}‘˜€ô‘˜¹½Áä ¤(€€€Í½ÉÑ•‘}‘™l‰}‘…Ñ”‰t€ôÍ½ÉÑ•‘}‘™l‰…Ñ”‰t¹µ…À¡Á…ÉÍ•}‘…Ñ•}Ù…±Õ”¤(€€€Í½ÉÑ•‘}‘˜€ôÍ½ÉÑ•‘}‘˜¹‘É½Á¹„¡ÍÕ‰Í•Ðõl‰}‘…Ñ”‰t¤¹Í½ÉÑ}Ù…±Õ•Ì ‰}‘…Ñ”ˆ°…Í•¹‘¥¹œõ…±Í”¤(€€€¥˜±•¸¡Í½ÉÑ•‘}‘˜¤€ð€Èè(€€€€€€€É•ÑÕÉ¸íô(€€€±…Ñ•ÍÐ€ôÍ½ÉÑ•‘}‘˜¹¥±½lÁt(€€€‰…Í”€ôÍ½ÉÑ•‘}‘˜¹¥±½mµ¥¸ Ð°±•¸¡Í½ÉÑ•‘}‘˜¤€´€Ä¥t(€€€¡…¹•Ì€ôíô(€€€™½È±…‰•°¥¸l‰Q½À€Ô€”ˆ°€‰Q½À€ÄÀ€”ˆ°€‰MÑ…­”¥¸ML€”‰tè(€€€€€€€±…Ñ•ÍÑ}Ù…±Õ”€ôÑ½}¹Õµ‰•È¡±…Ñ•ÍÐ¹•Ð¡±…‰•°¤¤(€€€€€€€‰…Í•}Ù…±Õ”€ôÑ½}¹Õµ‰•È¡‰…Í”¹•Ð¡±…‰•°¤¤(€€€€€€€¥˜±…Ñ•ÍÑ}Ù…±Õ”¥Ì9½¹”½È‰…Í•}Ù…±Õ”¥Ì9½¹”è(€€€€€€€€€€€¡…¹•Ím±…‰•±t€ô€‰¹½Ð…Ù…¥±…‰±”ˆ(€€€€€€€•±Í”è(€€€€€€€€€€€¡…¹•Ím±…‰•±t€ô˜‰í±…Ñ•ÍÑ}Ù…±Õ”€´‰…Í•}Ù…±Õ”è¬¸É™ôÁÁÐ€¡íÍ…™•}ÍÑÈ¡‰…Í”¹•Ð …Ñ”œ¤¥ôÑ¼íÍ…™•}ÍÑÈ¡±…Ñ•ÍÐ¹•Ð …Ñ”œ¤¥ô¤ˆ(€€€É•ÑÕÉ¸¡…¹•Ì(()‘•˜Ù…±¥‘…Ñ•}½¹•¹ÑÉ…Ñ¥½¸¡Á…ÉÍ•èA…ÉÍ•‘ML¤€´ø9½¹”è(€€€¥˜Á…ÉÍ•¹½¹•¹ÑÉ…Ñ¥½¹}Ñ…‰±”¹•µÁÑäè(€€€€€€€É•ÑÕÉ¸(€€€Ù…±Õ•}½±Õµ¹Ì€ôl‰Q½À€Ô€”ˆ°€‰Q½À€ÄÀ€”ˆ°€‰Q½À€ÄÀ€¬9%@€”ˆ°€‰MÑ…­”¥¸ML€”‰t(€€€™½È¥‘à°É½Ü¥¸Á…ÉÍ•¹½¹•¹ÑÉ…Ñ¥½¹}Ñ…‰±”¹¥Ñ•ÉÉ½ÝÌ ¤è(€€€€€€€‘…Ñ”€ôÍ…™•}ÍÑÈ¡É½Ü¹•Ð ‰…Ñ”ˆ¤¤½È˜‰É½Üí¥‘à€¬€Åôˆ(€€€€€€€™½È½±Õµ¸¥¸Ù…±Õ•}½±Õµ¹Ìè(€€€€€€€€€€€Ù…±Õ”€ôÑ½}¹Õµ‰•È¡É½Ü¹•Ð¡½±Õµ¸¤¤(€€€€€€€€€€€¥˜Ù…±Õ”¥Ì9½¹”è(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€€€€€¥˜Ù…±Õ”€ð€À½ÈÙ…±Õ”€ø€ÄÀÀè(€€€€€€€€€€€€€€€Á…ÉÍ•¹…¹…±åÍ¥Í}Ý…É¹¥¹Ì¹…ÁÁ•¹ (€€€€€€€€€€€€€€€€€€€˜‰‰¹½Éµ…°½¹•¹ÑÉ…Ñ¥½¸Ù…±Õ”èí‘…Ñ•ôí½±Õµ¹ô€ôíÉ½Ü¹•Ð¡½±Õµ¸¥ô¸áÁ•Ñ•É…¹”¥Ì€À´ÄÀÀ¸ˆ(€€€€€€€€€€€€€€€€¤(()‘•˜™…±±‰…­}½¹•¹ÑÉ…Ñ¥½¹}™É½µ}¡½±‘¥¹Ì¡Á…ÉÍ•èA…ÉÍ•‘ML°É•ÍÕ±Ðè•Ñ¡I•ÍÕ±Ðð9½¹”¤€´ø9½¹”è(€€€¥˜¹½ÐÁ…ÉÍ•¹½¹•¹ÑÉ…Ñ¥½¹}Ñ…‰±”¹•µÁÑä½ÈÁ…ÉÍ•¹¡½±‘¥¹Í}Ñ…‰±”¹•µÁÑäè(€€€€€€€É•ÑÕÉ¸(€€€Á…ÉÍ•¹½¹•¹ÑÉ…Ñ¥½¹}Ñ…‰±”€ôÁ¹…Ñ…É…µ” (€€€€€€€l(€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€‰…Ñ”ˆèÁ…ÉÍ•¹¡½±‘¥¹Í}‘…Ñ…}‘…Ñ”½È€‰ÕÉÉ•¹Ð¡½±‘¥¹ÌÁ…”ˆ°(€€€€€€€€€€€€€€€€‰Q½À€Ô€”ˆèÁ…ÉÍ•¹Ñ½ÀÕ}ÕµÕ±…Ñ¥Ù•}ÁÐ°(€€€€€€€€€€€€€€€€‰Q½À€ÄÀ€”ˆèÁ…ÉÍ•¹Ñ½ÀÄÁ}ÕµÕ±…Ñ¥Ù•}ÁÐ°(€€€€€€€€€€€€€€€€‰Q½À€ÄÀ€¬9%@€”ˆè€ˆˆ°(€€€€€€€€€€€€€€€€‰MÑ…­”¥¸ML€”ˆèÁ…ÉÍ•¹Ñ½Ñ…±}¥¹}…ÍÍ}ÁÐ°(€€€€€€€€€€€ô(€€€€€€€t(€€€€¤(€€€Á…ÉÍ•¹½¹•¹ÑÉ…Ñ¥½¹}±…Ñ•ÍÑ}‘…Ñ”€ôÁ…ÉÍ•¹¡½±‘¥¹Í}‘…Ñ…}‘…Ñ”½È€‰ÕÉÉ•¹Ð¡½±‘¥¹ÌÁ…”ˆ(€€€Í•Ñ¥½¸€ôÁ…ÉÍ•¹Í•Ñ¥½¹}Á…ÉÍ•Ì¹Í•Ñ‘•™…Õ±Ð ‰½¹•¹ÑÉ…Ñ¥½¸ˆ°M•Ñ¥½¹A…ÉÍ” ‰½¹•¹ÑÉ…Ñ¥½¸ˆ¤¤(€€€Í•Ñ¥½¸¹ÍÑ…ÑÕÌ€ô€‰Á…ÉÑ¥…°ÍÕ•ÍÌˆ(€€€Í•Ñ¥½¸¹±…Ñ•ÍÑ}‘…Ñ”€ôÁ…ÉÍ•¹½¹•¹ÑÉ…Ñ¥½¹}±…Ñ•ÍÑ}‘…Ñ”(€€€Í•Ñ¥½¸¹•ÉÉ½È€ô€‰½¹•¹ÑÉ…Ñ¥½¸Á…”™…¥±•ìQ½À€Ô€¼Q½À€ÄÀ•ÍÑ¥µ…Ñ•™É½´!½±‘¥¹ÌÑ…‰±”¸ˆ(€€€¥˜É•ÍÕ±Ðè(€€€€€€€Í•Ñ¥½¸¹Í•±•Ñ•‘}Ñ…‰±•}¥¹‘•à€ô9½¹”(()‘•˜Õ¹…Ù…¥±…‰±”¡Ù…±Õ”èÍÑÈ°É•…Í½¸èÍÑÈ¤€´øÍÑÈè(€€€É•ÑÕÉ¸Ù…±Õ”¥˜Ù…±Õ”•±Í”˜‰¹½Ð…Ù…¥±…‰±”‰•…ÕÍ”íÉ•…Í½¹ôˆ(()‘•˜…‘‘}É½ÍÍ}Í•Ñ¥½¹}Ý…É¹¥¹Ì¡Á…ÉÍ•èA…ÉÍ•‘ML¤€´ø9½¹”è(€€€¥˜¹½ÐÁ…ÉÍ•¹½¹•¹ÑÉ…Ñ¥½¹}Ñ…‰±”¹•µÁÑä…¹Á…ÉÍ•¹¡½±‘¥¹Í}Ñ…‰±”¹•µÁÑäè(€€€€€€€Á…ÉÍ•¹…¹…±åÍ¥Í}Ý…É¹¥¹Ì¹…ÁÁ•¹ ‰½¹•¹ÑÉ…Ñ¥½¸ÍÕ••‘•°‰ÕÐ!½±‘¥¹Ì™…¥±•¸Õ±°‰É½­•Èµ±•Ù•°…¹…±åÍ¥Ì¥Ì¥¹½µÁ±•Ñ”¸ˆ¤(€€€¥˜¹½ÐÁ…ÉÍ•¹‰¥}¡…¹•Í}Ñ…‰±”¹•µÁÑä…¹Á…ÉÍ•¹¡…¹•Í}Ñ…‰±”¹•µÁÑäè(€€€€€€€Á…ÉÍ•¹…¹…±åÍ¥Í}Ý…É¹¥¹Ì¹…ÁÁ•¹ ‰	¥œ¡…¹•ÌÍÕ••‘•°‰ÕÐ‘…¥±ä¡…¹•Ì™…¥±•¸I••¹Ð‘…¥±äµ½Ù•µ•¹Ð…¹¹½Ð‰”½¹™¥Éµ•¸ˆ¤(()‘•˜}…¹¹½Ñ…Ñ•}™É…µ•}‘…Ñ•Ì (€€€Á…ÉÍ•èA…ÉÍ•‘ML°(€€€Í•Ñ¥½¸èÍÑÈ°(€€€™É…µ”èÁ¹…Ñ…É…µ”°(€€€‘•™…Õ±Ñ}‘…Ñ”èÍÑÈ€ô€ˆˆ°(¤€´øÁ¹…Ñ…É…µ”è(€€€¥˜™É…µ”¥Ì9½¹”½È™É…µ”¹•µÁÑäè(€€€€€€€É•ÑÕÉ¸™É…µ”(€€€É•½É‘Ì°Ý…É¹¥¹Ì€ô…¹¹½Ñ…Ñ•}É•½É‘Ì¡™É…µ”¹Ñ½}‘¥Ð¡½É¥•¹Ðô‰É•½É‘Ìˆ¤°Í•Ñ¥½¸°‘•™…Õ±Ñ}‘…Ñ”õ‘•™…Õ±Ñ}‘…Ñ”¤(€€€™½ÈÝ…É¹¥¹œ¥¸Ý…É¹¥¹Ìè(€€€€€€€¥˜Ý…É¹¥¹œ¹½Ð¥¸Á…ÉÍ•¹…¹…±åÍ¥Í}Ý…É¹¥¹Ìè(€€€€€€€€€€€Á…ÉÍ•¹…¹…±åÍ¥Í}Ý…É¹¥¹Ì¹…ÁÁ•¹¡Ý…É¹¥¹œ¤(€€€É•ÑÕÉ¸Á¹…Ñ…É…µ”¡É•½É‘Ì¤(()‘•˜…ÁÁ±å}‘…Ñ•}Í•µ…¹Ñ¥Ì¡Á…ÉÍ•èA…ÉÍ•‘ML¤€´ø9½¹”è(€€€€ˆˆ‰ÑÑ… •áÁ±¥¥ÐÍ½ÕÉ”½¥µÁ±¥•‘…Ñ•Ì½¹”™½È•Ù•Éä½ÕÑÁÕÐÁ…Ñ ¸ˆˆˆ(€€€Á…ÉÍ•¹‘…Ñ•}‰…Í¥Í}‰å}Í•Ñ¥½¸€ôì(€€€€€€€Í•Ñ¥½¸¹±½Ý•È ¤¹É•Á±…” ˆ€ˆ°€‰|ˆ¤è‰…Í¥Ì(€€€€€€€™½ÈÍ•Ñ¥½¸°‰…Í¥Ì¥¸MQ%=9}Q}	M%L¹¥Ñ•µÌ ¤(€€€€€€€¥˜Í•Ñ¥½¸¥¸ì‰!½±‘¥¹Ìˆ°€‰¡…¹•Ìˆ°€‰	¥œ¡…¹•Ìˆ°€‰½¹•¹ÑÉ…Ñ¥½¸‰ô(€€€ô(€€€Á…ÉÍ•¹Í•ÑÑ±•µ•¹Ñ}¹½Ñ”€ôMQQ159Q}9=Q((€€€Á…ÉÍ•¹¡½±‘¥¹Í}Ñ…‰±”€ô}…¹¹½Ñ…Ñ•}™É…µ•}‘…Ñ•Ì (€€€€€€€Á…ÉÍ•°€‰!½±‘¥¹Ìˆ°Á…ÉÍ•¹¡½±‘¥¹Í}Ñ…‰±”°Á…ÉÍ•¹¡½±‘¥¹Í}‘…Ñ…}‘…Ñ”(€€€€¤(€€€Á…ÉÍ•¹¡…¹•Í}Ñ…‰±”€ô}…¹¹½Ñ…Ñ•}™É…µ•}‘…Ñ•Ì (€€€€€€€Á…ÉÍ•°€‰¡…¹•Ìˆ°Á…ÉÍ•¹¡…¹•Í}Ñ…‰±”°Á…ÉÍ•¹¡…¹•Í}ÑÉ…‘¥¹}‘…Ñ”(€€€€¤(€€€Á…ÉÍ•¹‰¥}¡…¹•Í}Ñ…‰±”€ô}…¹¹½Ñ…Ñ•}™É…µ•}‘…Ñ•Ì (€€€€€€€Á…ÉÍ•°€‰	¥œ¡…¹•Ìˆ°Á…ÉÍ•¹‰¥}¡…¹•Í}Ñ…‰±”°Á…ÉÍ•¹‰¥}¡…¹•Í}±…Ñ•ÍÑ}‘…Ñ”(€€€€¤(€€€Á…ÉÍ•¹½¹•¹ÑÉ…Ñ¥½¹}Ñ…‰±”€ô}…¹¹½Ñ…Ñ•}™É…µ•}‘…Ñ•Ì (€€€€€€€Á…ÉÍ•°€‰½¹•¹ÑÉ…Ñ¥½¸ˆ°Á…ÉÍ•¹½¹•¹ÑÉ…Ñ¥½¹}Ñ…‰±”°Á…ÉÍ•¹½¹•¹ÑÉ…Ñ¥½¹}±…Ñ•ÍÑ}‘…Ñ”(€€€€¤(€€€Á…ÉÍ•¹ÁÉ¥•}¡¥ÍÑ½Éå}Ñ…‰±”€ô}…¹¹½Ñ…Ñ•}™É…µ•}‘…Ñ•Ì (€€€€€€€Á…ÉÍ•°€‰AÉ¥”!¥ÍÑ½Éäˆ°Á…ÉÍ•¹ÁÉ¥•}¡¥ÍÑ½Éå}Ñ…‰±”°Á…ÉÍ•¹ÁÉ¥•}¡¥ÍÑ½Éå}±…Ñ•ÍÑ}‘…Ñ”(€€€€¤((€€€€Œ½¹•¹ÑÉ…Ñ¥½¸¥Ì¥¹‘•Á•¹‘•¹Ñ±äÍÕ™™¥¥•¹Ð™½ÈQ½À€Ô€¼Q½À€ÄÀ¸¼¹½Ð(€€€€Œ‰±…¹¬Ñ¡•Í”Ù…±Õ•Ìµ•É•±ä‰•…ÕÍ”‰É½­•Èµ±•Ù•°!½±‘¥¹Ì™…¥±•¸(€€€¥˜¹½ÐÁ…ÉÍ•¹½¹•¹ÑÉ…Ñ¥½¹}Ñ…‰±”¹•µÁÑäè(€€€€€€€±…Ñ•ÍÑ}½¹•¹ÑÉ…Ñ¥½¸€ôÁ…ÉÍ•¹½¹•¹ÑÉ…Ñ¥½¹}Ñ…‰±”¹Í½ÉÑ}Ù…±Õ•Ì (€€€€€€€€€€€€‰…ÍÍ}‘…Ñ”ˆ°…Í•¹‘¥¹œõ…±Í”°¹…}Á½Í¥Ñ¥½¸ô‰±…ÍÐˆ(€€€€€€€€¤¹¥±½lÁt(€€€€€€€Á…ÉÍ•¹Ñ½ÀÕ}ÕµÕ±…Ñ¥Ù•}ÁÐ€ôÁ…ÉÍ•¹Ñ½ÀÕ}ÕµÕ±…Ñ¥Ù•}ÁÐ½ÈÁ•É•¹Ñ}Ñ•áÐ (€€€€€€€€€€€±…Ñ•ÍÑ}½¹•¹ÑÉ…Ñ¥½¸¹•Ð ‰Q½À€Ô€”ˆ°€ˆˆ¤(€€€€€€€€¤(€€€€€€€Á…ÉÍ•¹Ñ½ÀÄÁ}ÕµÕ±…Ñ¥Ù•}ÁÐ€ôÁ…ÉÍ•¹Ñ½ÀÄÁ}ÕµÕ±…Ñ¥Ù•}ÁÐ½ÈÁ•É•¹Ñ}Ñ•áÐ (€€€€€€€€€€€±…Ñ•ÍÑ}½¹•¹ÑÉ…Ñ¥½¸¹•Ð ‰Q½À€ÄÀ€”ˆ°€ˆˆ¤(€€€€€€€€¤((€€€‘•É¥Ù…Ñ¥½¹Ì€ôì(€€€€€€€€‰!½±‘¥¹Ìˆè‘•É¥Ù•}‘…Ñ•Ì¡Á…ÉÍ•¹¡½±‘¥¹Í}‘…Ñ…}‘…Ñ”°MQ%=9}Q}	M%Ml‰!½±‘¥¹Ì‰t¤°(€€€€€€€€‰¡…¹•Ìˆè‘•É¥Ù•}‘…Ñ•Ì¡Á…ÉÍ•¹¡…¹•Í}ÑÉ…‘¥¹}‘…Ñ”°MQ%=9}Q}	M%Ml‰¡…¹•Ì‰t¤°(€€€€€€€€‰	¥œ¡…¹•Ìˆè‘•É¥Ù•}‘…Ñ•Ì¡Á…ÉÍ•¹‰¥}¡…¹•Í}±…Ñ•ÍÑ}‘…Ñ”°MQ%=9}Q}	M%Ml‰	¥œ¡…¹•Ì‰t¤°(€€€€€€€€‰½¹•¹ÑÉ…Ñ¥½¸ˆè‘•É¥Ù•}‘…Ñ•Ì (€€€€€€€€€€€Á…ÉÍ•¹½¹•¹ÑÉ…Ñ¥½¹}±…Ñ•ÍÑ}‘…Ñ”°MQ%=9}Q}	M%Ml‰½¹•¹ÑÉ…Ñ¥½¸‰t(€€€€€€€€¤°(€€€ô(€€€Á…ÉÍ•¹¡½±‘¥¹Í}¥µÁ±¥•‘}ÑÉ…‘•}‘…Ñ”€ô‘•É¥Ù…Ñ¥½¹Íl‰!½±‘¥¹Ì‰t¹¥µÁ±¥•‘}ÑÉ…‘•}‘…Ñ”(€€€Á…ÉÍ•¹¡…¹•Í}¥µÁ±¥•‘}ÑÉ…‘•}‘…Ñ”€ô‘•É¥Ù…Ñ¥½¹Íl‰¡…¹•Ì‰t¹¥µÁ±¥•‘}ÑÉ…‘•}‘…Ñ”(€€€Á…ÉÍ•¹‰¥}¡…¹•Í}¥µÁ±¥•‘}ÑÉ…‘•}‘…Ñ”€ô‘•É¥Ù…Ñ¥½¹Íl‰	¥œ¡…¹•Ì‰t¹¥µÁ±¥•‘}ÑÉ…‘•}‘…Ñ”(€€€Á…ÉÍ•¹½¹•¹ÑÉ…Ñ¥½¹}¥µÁ±¥•‘}ÑÉ…‘•}‘…Ñ”€ô‘•É¥Ù…Ñ¥½¹Íl‰½¹•¹ÑÉ…Ñ¥½¸‰t¹¥µÁ±¥•‘}ÑÉ…‘•}‘…Ñ”((€€€™½ÈÍ•Ñ¥½¸°‘•É¥Ù…Ñ¥½¸¥¸‘•É¥Ù…Ñ¥½¹Ì¹¥Ñ•µÌ ¤è(€€€€€€€¥˜‘•É¥Ù…Ñ¥½¸¹…ÍÍ}‘…Ñ”…¹‘•É¥Ù…Ñ¥½¸¹Ý…É¹¥¹œè(€€€€€€€€€€€Ý…É¹¥¹œ€ô˜‰íÍ•Ñ¥½¹ôèí‘•É¥Ù…Ñ¥½¸¹Ý…É¹¥¹ôˆ(€€€€€€€€€€€¥˜Ý…É¹¥¹œ¹½Ð¥¸Á…ÉÍ•¹…¹…±åÍ¥Í}Ý…É¹¥¹Ìè(€€€€€€€€€€€€€€€Á…ÉÍ•¹…¹…±åÍ¥Í}Ý…É¹¥¹Ì¹…ÁÁ•¹¡Ý…É¹¥¹œ¤((€€€Á…ÉÍ•¹‘…Ñ…}…Í}½™}ÑÉ…‘¥¹}‘…Ñ”€ô¹•áÐ (€€€€€€€€ (€€€€€€€€€€€Ù…±Õ”(€€€€€€€€€€€™½ÈÙ…±Õ”¥¸€ (€€€€€€€€€€€€€€€Á…ÉÍ•¹¡½±‘¥¹Í}¥µÁ±¥•‘}ÑÉ…‘•}‘…Ñ”°(€€€€€€€€€€€€€€€Á…ÉÍ•¹½¹•¹ÑÉ…Ñ¥½¹}¥µÁ±¥•‘}ÑÉ…‘•}‘…Ñ”°(€€€€€€€€€€€€€€€Á…ÉÍ•¹¡…¹•Í}¥µÁ±¥•‘}ÑÉ…‘•}‘…Ñ”°(€€€€€€€€€€€€€€€Á…ÉÍ•¹‰¥}¡…¹•Í}¥µÁ±¥•‘}ÑÉ…‘•}‘…Ñ”°(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜Ù…±Õ”(€€€€€€€€¤°(€€€€€€€Õ¹…Ù…¥±…‰±•}‘…Ñ…}…Í}½˜ ‰¹¼‘…Ñ•!½±‘¥¹Ì°½¹•¹ÑÉ…Ñ¥½¸°¡…¹•Ì½È	¥œ¡…¹•ÌÉ½ÜÁ…ÉÍ•ˆ¤°(€€€€¤(()‘•˜Á…ÉÍ•}É•ÍÕ±ÑÌ (€€€¥ÍÍÕ•}¥èÍÑÈ°(€€€É•ÍÕ±ÑÌè‘¥ÑmÍÑÈ°•Ñ¡I•ÍÕ±Ñt°(€€€ÍÑ½­}½‘”èÍÑÈ€ô€ˆˆ°(€€€¥‘}±½½­ÕÁ}µ•Ñ¡½èÍÑÈ€ô€ˆˆ°(€€€¥‘}±½½­ÕÁ}ÍÑ…ÑÕÌèÍÑÈ€ô€ˆˆ°(€€€Í•±•Ñ•‘}¥¹‘¥•Ìè‘¥ÑmÍÑÈ°¥¹Ñtð9½¹”€ô9½¹”°(€€€Í½ÕÉ•}µ•Ñ…‘…Ñ„è‘¥ÑmÍÑÈ°¹åtð9½¹”€ô9½¹”°(¤€´øA…ÉÍ•‘MLè(€€€Á…ÉÍ•€ôA…ÉÍ•‘ML (€€€€€€€¥ÍÍÕ•}¥õ¥ÍÍÕ•}¥°(€€€€€€€ÍÑ½­}½‘”õÍÑ½­}½‘”°(€€€€€€€¥‘}±½½­ÕÁ}µ•Ñ¡½õ¥‘}±½½­ÕÁ}µ•Ñ¡½°(€€€€€€€¥‘}±½½­ÕÁ}ÍÑ…ÑÕÌõ¥‘}±½½­ÕÁ}ÍÑ…ÑÕÌ°(€€€€¤(€€€™•Ñ¡•‘}Ñ¥µ•Ì€ôm¥Ñ•´¹™•Ñ¡•‘}Ñ¥µ”™½È¥Ñ•´¥¸É•ÍÕ±ÑÌ¹Ù…±Õ•Ì ¤¥˜¥Ñ•´¹™•Ñ¡•‘}Ñ¥µ•t(€€€Á…ÉÍ•¹™•Ñ¡•‘}Ñ¥µ”€ôµ…à¡™•Ñ¡•‘}Ñ¥µ•Ì¤¥˜™•Ñ¡•‘}Ñ¥µ•Ì•±Í”€ˆˆ(€€€¥˜Í½ÕÉ•}µ•Ñ…‘…Ñ„è(€€€€€€€Á…ÉÍ•¹Í½ÕÉ”€ôÍ…™•}ÍÑÈ¡Í½ÕÉ•}µ•Ñ…‘…Ñ„¹•Ð ‰Í½ÕÉ”ˆ¤¤(€€€€€€€Á…ÉÍ•¹µ¥ÉÉ½É}ÍÑ…ÑÕÌ€ôÍ…™•}ÍÑÈ¡Í½ÕÉ•}µ•Ñ…‘…Ñ„¹•Ð ‰µ¥ÉÉ½É}ÍÑ…ÑÕÌˆ¤¤(€€€€€€€Á…ÉÍ•¹µ¥ÉÉ½É}‰…Í•}ÕÉ°€ôÍ…™•}ÍÑÈ¡Í½ÕÉ•}µ•Ñ…‘…Ñ„¹•Ð ‰µ¥ÉÉ½É}‰…Í•}ÕÉ°ˆ¤¤(€€€€€€€ÑÉäè(€€€€€€€€€€€Á…ÉÍ•¹¡¥ÍÑ½Éå}‘•ÁÑ¡}‘…åÌ€ô¥¹Ð¡Í½ÕÉ•}µ•Ñ…‘…Ñ„¹•Ð ‰¡¥ÍÑ½Éå}‘•ÁÑ¡}‘…åÌˆ¤½È€À¤(€€€€€€€•á•ÁÐ€¡QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è(€€€€€€€€€€€Á…ÉÍ•¹¡¥ÍÑ½Éå}‘•ÁÑ¡}‘…åÌ€ô€À(€€€€€€€Á…ÉÍ•¹‘‰}É•ÍÑ½É•‘}™É½µ}‰…­ÕÀ€ô‰½½°¡Í½ÕÉ•}µ•Ñ…‘…Ñ„¹•Ð ‰‘‰}É•ÍÑ½É•‘}™É½µ}‰…­ÕÀˆ°…±Í”¤¤((€€€¥˜É•ÍÕ±ÑÌ¹•Ð ‰½µÁ…¹ä€¼½É‘…Ñ„ˆ¤è(€€€€€€€Á…ÉÍ•}½µÁ…¹ä¡É•ÍÕ±ÑÍl‰½µÁ…¹ä€¼½É‘…Ñ„‰t°Á…ÉÍ•°Í•±•Ñ•‘}¥¹‘¥•Ì¤(€€€¥˜É•ÍÕ±ÑÌ¹•Ð ‰!½±‘¥¹Ìˆ¤è(€€€€€€€¥˜É•ÍÕ±ÑÍl‰!½±‘¥¹Ì‰t¹½¬è(€€€€€€€€€€€Á…ÉÍ•}¡½±‘¥¹Ì¡É•ÍÕ±ÑÍl‰!½±‘¥¹Ì‰t°Á…ÉÍ•°Í•±•Ñ•‘}¥¹‘¥•Ì¤(€€€€€€€•±Í”è(€€€€€€€€€€€Á…ÉÍ•¹Í•Ñ¥½¹}Á…ÉÍ•Íl‰!½±‘¥¹Ì‰t€ôM•Ñ¥½¹A…ÉÍ” ‰!½±‘¥¹Ìˆ°ÍÑ…ÑÕÌô‰™…¥±•ˆ°•ÉÉ½ÈõÉ•ÍÕ±ÑÍl‰!½±‘¥¹Ì‰t¹•ÉÉ½É}µ•ÍÍ…”¤(€€€¥˜É•ÍÕ±ÑÌ¹•Ð ‰¡…¹•Ìˆ¤è(€€€€€€€¥˜É•ÍÕ±ÑÍl‰¡…¹•Ì‰t¹½¬è(€€€€€€€€€€€Á…ÉÍ•}¡…¹•Ì¡É•ÍÕ±ÑÍl‰¡…¹•Ì‰t°Á…ÉÍ•°Í•±•Ñ•‘}¥¹‘¥•Ì¤(€€€€€€€•±Í”è(€€€€€€€€€€€Á…ÉÍ•¹Í•Ñ¥½¹}Á…ÉÍ•Íl‰¡…¹•Ì‰t€ôM•Ñ¥½¹A…ÉÍ” ‰¡…¹•Ìˆ°ÍÑ…ÑÕÌô‰™…¥±•ˆ°•ÉÉ½ÈõÉ•ÍÕ±ÑÍl‰¡…¹•Ì‰t¹•ÉÉ½É}µ•ÍÍ…”¤(€€€¥˜É•ÍÕ±ÑÌ¹•Ð ‰	¥œ¡…¹•Ìˆ¤è(€€€€€€€¥˜É•ÍÕ±ÑÍl‰	¥œ¡…¹•Ì‰t¹½¬è(€€€€€€€€€€€Á…ÉÍ•}‰¥}¡…¹•Ì¡É•ÍÕ±ÑÍl‰	¥œ¡…¹•Ì‰t°Á…ÉÍ•°Í•±•Ñ•‘}¥¹‘¥•Ì¤(€€€€€€€•±Í”è(€€€€€€€€€€€Á…ÉÍ•¹Í•Ñ¥½¹}Á…ÉÍ•Íl‰	¥œ¡…¹•Ì‰t€ôM•Ñ¥½¹A…ÉÍ” ‰	¥œ¡…¹•Ìˆ°ÍÑ…ÑÕÌô‰™…¥±•ˆ°•ÉÉ½ÈõÉ•ÍÕ±ÑÍl‰	¥œ¡…¹•Ì‰t¹•ÉÉ½É}µ•ÍÍ…”¤(€€€¥˜É•ÍÕ±ÑÌ¹•Ð ‰½¹•¹ÑÉ…Ñ¥½¸ˆ¤è(€€€€€€€¥˜É•ÍÕ±ÑÍl‰½¹•¹ÑÉ…Ñ¥½¸‰t¹½¬è(€€€€€€€€€€€Á…ÉÍ•}½¹•¹ÑÉ…Ñ¥½¸¡É•ÍÕ±ÑÍl‰½¹•¹ÑÉ…Ñ¥½¸‰t°Á…ÉÍ•°Í•±•Ñ•‘}¥¹‘¥•Ì¤(€€€€€€€•±Í”è(€€€€€€€€€€€Á…ÉÍ•¹Í•Ñ¥½¹}Á…ÉÍ•Íl‰½¹•¹ÑÉ…Ñ¥½¸‰t€ôM•Ñ¥½¹A…ÉÍ” ‰½¹•¹ÑÉ…Ñ¥½¸ˆ°ÍÑ…ÑÕÌô‰™…¥±•ˆ°•ÉÉ½ÈõÉ•ÍÕ±ÑÍl‰½¹•¹ÑÉ…Ñ¥½¸‰t¹•ÉÉ½É}µ•ÍÍ…”¤(€€€¥˜É•ÍÕ±ÑÌ¹•Ð ‰AÉ¥”!¥ÍÑ½Éäˆ¤è(€€€€€€€¥˜É•ÍÕ±ÑÍl‰AÉ¥”!¥ÍÑ½Éä‰t¹½¬è(€€€€€€€€€€€Á…ÉÍ•}ÁÉ¥•}¡¥ÍÑ½Éä¡É•ÍÕ±ÑÍl‰AÉ¥”!¥ÍÑ½Éä‰t°Á…ÉÍ•°Í•±•Ñ•‘}¥¹‘¥•Ì¤(€€€€€€€•±Í”è(€€€€€€€€€€€Á…ÉÍ•¹Í•Ñ¥½¹}Á…ÉÍ•Íl‰AÉ¥”!¥ÍÑ½Éä‰t€ôM•Ñ¥½¹A…ÉÍ” ‰AÉ¥”!¥ÍÑ½Éäˆ°ÍÑ…ÑÕÌô‰™…¥±•ˆ°•ÉÉ½ÈõÉ•ÍÕ±ÑÍl‰AÉ¥”!¥ÍÑ½Éä‰t¹•ÉÉ½É}µ•ÍÍ…”¤((€€€™…±±‰…­}½¹•¹ÑÉ…Ñ¥½¹}™É½µ}¡½±‘¥¹Ì¡Á…ÉÍ•°É•ÍÕ±ÑÌ¹•Ð ‰½¹•¹ÑÉ…Ñ¥½¸ˆ¤¤(€€€…ÁÁ±å}‘…Ñ•}Í•µ…¹Ñ¥Ì¡Á…ÉÍ•¤(€€€…‘‘}É½ÍÍ}Í•Ñ¥½¹}Ý…É¹¥¹Ì¡Á…ÉÍ•¤(€€€É•ÑÕÉ¸Á…ÉÍ•(()‘•˜‰Õ¥±‘}™•Ñ¡}ÍÕµµ…Éä¡Á…ÉÍ•èA…ÉÍ•‘ML°É•ÍÕ±ÑÌè‘¥ÑmÍÑÈ°•Ñ¡I•ÍÕ±Ñt¤€´øÁ¹…Ñ…É…µ”è(€€€É½ÝÌ€ômt(€€€™½ÈÍ•Ñ¥½¸¥¸MQ%=9Lè(€€€€€€€É•ÍÕ±Ð€ôÉ•ÍÕ±ÑÌ¹•Ð¡Í•Ñ¥½¸¤(€€€€€€€Á…ÉÍ”€ôÁ…ÉÍ•¹Í•Ñ¥½¹}Á…ÉÍ•Ì¹•Ð¡Í•Ñ¥½¸°M•Ñ¥½¹A…ÉÍ”¡Í•Ñ¥½¸¤¤(€€€€€€€ÍÑ…ÑÕÌ€ôÁ…ÉÍ”¹ÍÑ…ÑÕÌ(€€€€€€€¥˜É•ÍÕ±Ð…¹¹½ÐÉ•ÍÕ±Ð¹½¬è(€€€€€€€€€€€ÍÑ…ÑÕÌ€ô€‰™…¥±•ˆ(€€€€€€€É½ÝÌ¹…ÁÁ•¹ (€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€‰M•Ñ¥½¸ˆèÍ•Ñ¥½¸°(€€€€€€€€€€€€€€€€‰UI0ˆèÉ•ÍÕ±Ð¹ÕÉ°¥˜É•ÍÕ±Ð•±Í”€ˆˆ°(€€€€€€€€€€€€€€€€‰MÑ…ÑÕÌˆèÍÑ…ÑÕÌ°(€€€€€€€€€€€€€€€€‰Q…‰±•Ì™½Õ¹ˆè±•¸¡É•ÍÕ±Ð¹Ñ…‰±•Ì¤¥˜É•ÍÕ±Ð•±Í”€À°(€€€€€€€€€€€€€€€€‰M•±•Ñ•Ñ…‰±”¥¹‘•àˆèÁ…ÉÍ”¹Í•±•Ñ•‘}Ñ…‰±•}¥¹‘•à¥˜Á…ÉÍ”¹Í•±•Ñ•‘}Ñ…‰±•}¥¹‘•à¥Ì¹½Ð9½¹”•±Í”€ˆˆ°(€€€€€€€€€€€€€€€€‰1…Ñ•ÍÐ‘…Ñ”€¼‘…Ñ„‘…Ñ”ˆèÁ…ÉÍ”¹±…Ñ•ÍÑ}‘…Ñ”°(€€€€€€€€€€€€€€€€‰ÉÉ½ÈˆèÁ…ÉÍ”¹•ÉÉ½È½È€¡É•ÍÕ±Ð¹•ÉÉ½É}µ•ÍÍ…”¥˜É•ÍÕ±Ð…¹¹½ÐÉ•ÍÕ±Ð¹½¬•±Í”€ˆˆ¤°(€€€€€€€€€€€ô(€€€€€€€€¤(€€€É•ÑÕÉ¸Á¹…Ñ…É…µ”¡É½ÝÌ¤(