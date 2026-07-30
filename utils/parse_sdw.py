from __future__ import annotations

import re
from dataclasses import dataclass
from io import StringIO
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup


class SDWParseError(ValueError):
    pass


@dataclass
class SDWSnapshot:
    code: str
    date: str
    stock_name: str = ""
    issued_shares: str = ""
    rows: list[dict[str, Any]] | None = None


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def clean_number(value: Any) -> int:
    text = safe_text(value).replace(",", "")
    match = re.search(r"-?\d+", text)
    return int(match.group(0)) if match else 0


def clean_percent(value: Any) -> float | None:
    text = safe_text(value).replace(",", "").replace("%", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def normalize_date(value: str) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise SDWParseError(f"PARSE_ERROR: cannot parse SDW date {value!r}")
    return parsed.strftime("%Y-%m-%d")


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [
            " ".join(safe_text(part) for part in col if safe_text(part) and not str(part).startswith("Unnamed"))
            for col in out.columns
        ]
    else:
        out.columns = [safe_text(col) for col in out.columns]
    return out.dropna(how="all")


def _pick_column(df: pd.DataFrame, *needles: str) -> str | None:
    for col in df.columns:
        norm_col = re.sub(r"[^a-z0-9]+", " ", safe_text(col).lower())
        if all(needle.lower() in norm_col for needle in needles):
            return col
    return None


def _extract_metadata(html: str, code: str, query_date: str) -> tuple[str, str, str]:
    soup = BeautifulSoup(html or "", "lxml")
    text = soup.get_text("\n", strip=True)
    stock_name = ""
    issued_shares = ""
    date = query_date

    name_match = re.search(rf"\b{re.escape(code)}\b\s+([^\n]+)", text)
    if name_match:
        stock_name = safe_text(name_match.group(1))

    issued_match = re.search(r"Issued Shares\s*[:：]?\s*([0-9,]+)", text, flags=re.I)
    if issued_match:
        issued_shares = issued_match.group(1)

    date_match = re.search(r"Shareholding Date\s*[:：]?\s*([0-9]{4}[/\-][0-9]{1,2}[/\-][0-9]{1,2})", text, flags=re.I)
    if date_match:
        date = date_match.group(1)

    return stock_name, issued_shares, normalize_date(date)


def parse_sdw_snapshot(html: str, code: str, query_date: str) -> SDWSnapshot:
    if not html:
        raise SDWParseError("PARSE_ERROR: empty SDW HTML")

    tables = pd.read_html(StringIO(html))
    candidates: list[pd.DataFrame] = []
    for table in tables:
        df = _flatten_columns(table)
        joined = " ".join(map(str, df.columns)) + " " + df.head(5).astype(str).to_string(index=False)
        normalized = re.sub(r"[^a-z0-9]+", " ", joined.lower())
        if "participant" in normalized and "shareholding" in normalized:
            candidates.append(df)

    if not candidates:
        raise SDWParseError("SOURCE_CHANGED: no SDW participant shareholding table found")

    table = max(candidates, key=len)
    participant_id_col = _pick_column(table, "participant", "id")
    participant_name_col = _pick_column(table, "participant", "name")
    shares_col = _pick_column(table, "shareholding")
    pct_col = _pick_column(table, "%") or _pick_column(table, "percent") or _pick_column(table, "issued")

    if not participant_id_col or not participant_name_col or not shares_col:
        raise SDWParseError("SOURCE_CHANGED: SDW table columns do not match expected participant fields")

    rows: list[dict[str, Any]] = []
    for _, row in table.iterrows():
        participant_id = safe_text(row.get(participant_id_col))
        participant_name = safe_text(row.get(participant_name_col))
        if not participant_id or not participant_name or not re.search(r"\d", participant_id):
            continue
        shares = clean_number(row.get(shares_col))
        pct = clean_percent(row.get(pct_col)) if pct_col else None
        rows.append(
            {
                "participant_id": participant_id,
                "participant_name": participant_name,
                "shares": shares,
                "pct_of_issued": pct,
            }
        )

    if not rows:
        raise SDWParseError("PARSE_ERROR: SDW participant table parsed but no participant rows were extracted")

    stock_name, issued_shares, date = _extract_metadata(html, code, query_date)
    return SDWSnapshot(code=code, date=date, stock_name=stock_name, issued_shares=issued_shares, rows=rows)
