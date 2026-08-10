from __future__ import annotations

from io import BytesIO
import json

import pandas as pd

from .fetcher import FetchResult
from .parser import ParsedCCASS, build_fetch_summary, table_preview_records


COMMON_EXPORT_COLUMNS = [
    "section",
    "record_type",
    "row_meaning",
    "stock_code",
    "stock_name",
    "webbsite_issue_id",
    "fetched_time",
    "data_date_or_latest_date",
    "date_basis",
    "data_as_of_trading_date",
    "source_url",
    "fetch_status",
    "fetch_method",
    "http_status",
    "error_type",
    "error_message",
    "fallback_method_used",
    "section_row_count",
    "data_quality_status",
    "data_quality_warning",
]


SECTION_DATE_BASIS = {
    "Company / orgdata": "company_profile",
    "Holdings": "settlement_date",
    "Changes": "trading_date",
    "Big Changes": "settlement_date",
    "Concentration": "settlement_date",
    "Price History": "trading_date",
    "HKEX Announcements": "announcement_publish_time",
    "Corporate Events": "event_date",
    "Share Capital Changes": "event_date",
    "Buybacks": "transaction_date",
    "Managers F10": "profile_as_fetched",
}


def _parsed_value(parsed: ParsedCCASS, name: str, default=""):
    return getattr(parsed, name, default)


def _data_as_of_trading_date(parsed: ParsedCCASS) -> str:
    return str(
        _parsed_value(parsed, "data_as_of_trading_date", "")
        or parsed.changes_trading_date
        or parsed.price_history_latest_date
        or ""
    )


def _date_basis_map(parsed: ParsedCCASS) -> dict[str, str]:
    value = _parsed_value(parsed, "date_basis_by_section", None)
    if isinstance(value, dict) and value:
        return value
    return {
        section: basis
        for section, basis in SECTION_DATE_BASIS.items()
        if section in {"Holdings", "Changes", "Big Changes", "Concentration", "Price History"}
    }


def metadata_dict(parsed: ParsedCCASS) -> dict:
    return {
        "stock_code": parsed.stock_code,
        "stock_name": parsed.stock_name,
        "issue_id": parsed.issue_id,
        "id_lookup_method": parsed.id_lookup_method,
        "id_lookup_status": parsed.id_lookup_status,
        "source": parsed.source,
        "mirror_status": parsed.mirror_status,
        "mirror_base_url": parsed.mirror_base_url,
        "history_depth_days": parsed.history_depth_days,
        "db_restored_from_backup": parsed.db_restored_from_backup,
        "fetched_time": parsed.fetched_time,
        "data_as_of_trading_date": _data_as_of_trading_date(parsed),
        "date_basis_by_section": _date_basis_map(parsed),
        "settlement_note": _parsed_value(
            parsed,
            "settlement_note",
            "Holdings, Big Changes and Concentration use CCASS settlement dates; Changes and Price History use trading dates.",
        ),
        "holdings_data_date": parsed.holdings_data_date,
        "changes_date_range": parsed.changes_date_range,
        "changes_trading_date": parsed.changes_trading_date,
        "big_changes_latest_date": parsed.big_changes_latest_date,
        "concentration_latest_date": parsed.concentration_latest_date,
        "price_history_latest_date": parsed.price_history_latest_date,
        "price_source": parsed.price_source,
        "issued_securities": parsed.issued_securities,
        "total_in_ccass": parsed.total_in_ccass,
        "total_in_ccass_pct": parsed.total_in_ccass_pct,
        "securities_not_in_ccass": parsed.securities_not_in_ccass,
        "largest_participant": parsed.largest_participant,
        "top5_cumulative_pct": parsed.top5_cumulative_pct,
        "top10_cumulative_pct": parsed.top10_cumulative_pct,
        "latest_price": parsed.latest_price,
        "latest_price_volume": parsed.latest_price_volume,
        "latest_price_turnover": parsed.latest_price_turnover,
        "latest_price_vwap": parsed.latest_price_vwap,
    }


def parsed_to_json_ready(
    parsed: ParsedCCASS,
    results: dict[str, FetchResult],
    extras: dict | None = None,
) -> dict:
    return {
        "metadata": metadata_dict(parsed),
        "fetch_summary": build_fetch_summary(parsed, results).to_dict(orient="records"),
        "fetch_log": [result.to_log() for result in results.values()],
        "holdings": parsed.holdings_table.to_dict(orient="records"),
        "changes": parsed.changes_table.to_dict(orient="records"),
        "bigchanges": parsed.big_changes_table.to_dict(orient="records"),
        "concentration": parsed.concentration_table.to_dict(orient="records"),
        "price_history": parsed.price_history_table.to_dict(orient="records"),
        "supplementary": extras or {},
        "raw_table_previews": table_preview_records(results),
        "major_increases": parsed.major_increases,
        "major_decreases": parsed.major_decreases,
        "transfer_flags": parsed.transfer_flags,
        "analysis_warnings": parsed.analysis_warnings,
    }


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


# (section, row_meaning, extras key, source key in extras)
EXTRA_SECTIONS = [
    (
        "HKEX Announcements",
        "HKEX listed-company announcements returned for the selected period",
        "hkex_announcements",
        "hkex_announcements_url",
    ),
    (
        "Corporate Events",
        "Webb-site entitlement events (dividends, splits, rights)",
        "events",
        "events_url",
    ),
    (
        "Share Capital Changes",
        "Issued-share history: placements, option exercises, buyback cancellations (10jqka F10)",
        "share_changes",
        "equity_url",
    ),
    (
        "Buybacks",
        "Per-day share buyback records (10jqka F10)",
        "buybacks",
        "equity_url",
    ),
    (
        "Managers F10",
        "Current management with tenure, salary and biography (10jqka F10)",
        "managers_f10",
        "managers_url",
    ),
]


def _clean_data_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    keep = []
    for column in df.columns:
        label = str(column).strip()
        if not label or label.isdigit() or label.lower().startswith("unnamed:"):
            continue
        keep.append(column)
    return df.loc[:, keep].copy()


def _percentage_number(value) -> float | None:
    text = str(value or "").replace(",", "").replace("%", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _annotate_data_quality(section: str, df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["data_quality_status"] = "ok"
    out["data_quality_warning"] = ""
    if section != "Concentration" or out.empty:
        return out

    percent_columns = [
        "Top 5 %",
        "Top 10 %",
        "Top 10 + NCIP %",
        "Stake in CCASS %",
    ]
    for index, row in out.iterrows():
        warnings = []
        for column in percent_columns:
            if column not in out.columns:
                continue
            number = _percentage_number(row.get(column))
            if number is not None and not 0 <= number <= 100:
                warnings.append(f"{column}={row.get(column)} outside expected 0-100 range")
        if warnings:
            out.at[index, "data_quality_status"] = "warning"
            out.at[index, "data_quality_warning"] = "; ".join(warnings)
    return out


def _expected_source_url(parsed: ParsedCCASS, section: str) -> str:
    base = (parsed.mirror_base_url or "https://webb-database.com").rstrip("/")
    issue_id = str(parsed.issue_id or "").strip()
    stock_code = str(parsed.stock_code or "").strip()
    paths = {
        "Company / orgdata": f"/dbpub/orgdata.asp?code={stock_code}&Submit=current" if stock_code else "",
        "Holdings": f"/ccass/choldings.asp?i={issue_id}" if issue_id else "",
        "Changes": f"/ccass/chldchg.asp?i={issue_id}" if issue_id else "",
        "Big Changes": f"/ccass/bigchangesissue.asp?i={issue_id}" if issue_id else "",
        "Concentration": f"/ccass/cconchist.asp?i={issue_id}" if issue_id else "",
        "Price History": f"/dbpub/hpu.asp?i={issue_id}" if issue_id else "",
    }
    path = paths.get(section, "")
    return f"{base}{path}" if path else ""


def _result_source_url(parsed: ParsedCCASS, result: FetchResult | None, section: str) -> str:
    if result:
        return str(result.final_url or result.url or _expected_source_url(parsed, section))
    return _expected_source_url(parsed, section)


def _section_parse_message(parsed: ParsedCCASS, section: str) -> str:
    parse = parsed.section_parses.get(section)
    if not parse:
        return ""
    return str(parse.error or parse.message or "")


def _section_fetch_status(result: FetchResult | None, row_count: int) -> str:
    has_rows = row_count > 0
    if result and result.ok and has_rows:
        return "success"
    if result and result.ok:
        return "no_matching_table"
    if has_rows:
        return "partial_success"
    return "failed"


def _section_context(
    parsed: ParsedCCASS,
    section: str,
    description: str,
    data_date: str,
    result: FetchResult | None,
    row_count: int,
) -> dict:
    status = _section_fetch_status(result, row_count)
    parse_message = _section_parse_message(parsed, section)
    error_message = str(result.error_message or "") if result else ""
    if not error_message:
        error_message = parse_message
    if not error_message and status in {"failed", "no_matching_table"}:
        error_message = "No parsed rows were available for this section."
    return {
        "section": section,
        "row_meaning": description,
        "stock_code": parsed.stock_code,
        "stock_name": parsed.stock_name,
        "webbsite_issue_id": parsed.issue_id,
        "fetched_time": parsed.fetched_time,
        "data_date_or_latest_date": data_date,
        "date_basis": SECTION_DATE_BASIS.get(section, ""),
        "data_as_of_trading_date": _data_as_of_trading_date(parsed),
        "source_url": _result_source_url(parsed, result, section),
        "fetch_status": status,
        "fetch_method": result.method if result else (parsed.source or "not_fetched"),
        "http_status": result.status if result else "",
        "error_type": (
            result.error_type
            if result
            else ("PARSE_ERROR" if parse_message else ("NO_RESULT" if status == "failed" else ""))
        ),
        "error_message": error_message,
        "fallback_method_used": (
            result.fallback_method_used
            if result
            else (parsed.source if row_count else "")
        ),
        "section_row_count": row_count,
    }


def _section_data_frame(
    parsed: ParsedCCASS,
    section: str,
    description: str,
    data_date: str,
    df: pd.DataFrame,
    result: FetchResult | None,
) -> pd.DataFrame:
    out = _clean_data_columns(df)
    if out.empty:
        return out
    out = _annotate_data_quality(section, out)
    context = _section_context(parsed, section, description, data_date, result, len(out))
    context["record_type"] = "data"
    for column, value in context.items():
        out[column] = value
    ordered = COMMON_EXPORT_COLUMNS + [column for column in out.columns if column not in COMMON_EXPORT_COLUMNS]
    return out.reindex(columns=ordered)


def _record_date(record: dict) -> str:
    for key in (
        "Publish time",
        "publish_time",
        "Date",
        "date",
        "change_date",
        "announce_date",
        "ex_date",
        "announced",
        "tenure_from",
    ):
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def extras_frames(parsed: ParsedCCASS, extras: dict | None) -> list[pd.DataFrame]:
    frames = []
    for section, description, key, url_key in EXTRA_SECTIONS:
        records = (extras or {}).get(key) or []
        source_url = str((extras or {}).get(url_key, "") or "")
        status_context = {
            "section": section,
            "record_type": "fetch_status",
            "row_meaning": description,
            "stock_code": parsed.stock_code,
            "stock_name": parsed.stock_name,
            "webbsite_issue_id": parsed.issue_id,
            "fetched_time": parsed.fetched_time,
            "data_date_or_latest_date": _record_date(records[0]) if records else "",
            "date_basis": SECTION_DATE_BASIS.get(section, ""),
            "data_as_of_trading_date": _data_as_of_trading_date(parsed),
            "source_url": source_url,
            "fetch_status": "success" if records else "no_records_returned",
            "fetch_method": "supplementary_source",
            "section_row_count": len(records),
            "data_quality_status": "not_applicable",
            "data_quality_warning": "",
        }
        frames.append(pd.DataFrame([status_context]).reindex(columns=COMMON_EXPORT_COLUMNS))
        if not records:
            continue
        out = _clean_data_columns(pd.DataFrame(records))
        out["section"] = section
        out["record_type"] = "data"
        out["row_meaning"] = description
        out["stock_code"] = parsed.stock_code
        out["stock_name"] = parsed.stock_name
        out["webbsite_issue_id"] = parsed.issue_id
        out["fetched_time"] = parsed.fetched_time
        out["data_date_or_latest_date"] = [_record_date(record) for record in records]
        out["date_basis"] = SECTION_DATE_BASIS.get(section, "")
        out["data_as_of_trading_date"] = _data_as_of_trading_date(parsed)
        out["source_url"] = source_url
        out["fetch_status"] = "success"
        out["fetch_method"] = "supplementary_source"
        out["section_row_count"] = len(out)
        out["data_quality_status"] = "ok"
        out["data_quality_warning"] = ""
        ordered = COMMON_EXPORT_COLUMNS + [column for column in out.columns if column not in COMMON_EXPORT_COLUMNS]
        frames.append(out.reindex(columns=ordered))
    return frames


def combined_stock_csv(
    parsed: ParsedCCASS,
    results: dict[str, FetchResult],
    extras: dict | None = None,
) -> bytes:
    """Create one analysis-ready, self-describing CSV for a stock.

    Every section receives a fetch-status row before its data rows. The file
    keeps source URL, fetch time, date basis and errors even when a page fails.
    """
    sections = [
        (
            "Company / orgdata",
            "Company identity and stock metadata used to resolve the Webb-site issue ID",
            "",
            parsed.company_table,
        ),
        (
            "Holdings",
            "Broker/participant holdings on the Holdings settlement date",
            parsed.holdings_data_date,
            parsed.holdings_table,
        ),
        (
            "Changes",
            "Daily CCASS participant holding changes on the stated trading date",
            parsed.changes_trading_date or parsed.changes_date_range,
            parsed.changes_table,
        ),
        (
            "Big Changes",
            "Large historical CCASS participant holding changes",
            parsed.big_changes_latest_date,
            parsed.big_changes_table,
        ),
        (
            "Concentration",
            "Top holder concentration history",
            parsed.concentration_latest_date,
            parsed.concentration_table,
        ),
        (
            "Price History",
            "Historical close price, volume, turnover and VWAP",
            parsed.price_history_latest_date,
            parsed.price_history_table,
        ),
    ]

    metadata = metadata_dict(parsed)
    metadata_row = {
        "section": "Metadata",
        "record_type": "metadata",
        "row_meaning": "File-level source, identity and date semantics for this export",
        "stock_code": parsed.stock_code,
        "stock_name": parsed.stock_name,
        "webbsite_issue_id": parsed.issue_id,
        "fetched_time": parsed.fetched_time,
        "data_as_of_trading_date": _data_as_of_trading_date(parsed),
        "fetch_status": "success",
        "section_row_count": 1,
        "data_quality_status": "not_applicable",
        "export_schema_version": "2.0",
        **metadata,
    }
    metadata_row["date_basis_by_section"] = json.dumps(
        metadata_row.get("date_basis_by_section", {}),
        ensure_ascii=False,
        sort_keys=True,
    )
    frames = [pd.DataFrame([metadata_row])]

    for section, description, data_date, frame in sections:
        df = _clean_data_columns(frame)
        result = results.get(section)
        context = _section_context(parsed, section, description, data_date, result, len(df))
        status_row = {
            **context,
            "record_type": "fetch_status",
            "data_quality_status": "not_applicable",
            "data_quality_warning": "",
        }
        frames.append(pd.DataFrame([status_row]).reindex(columns=COMMON_EXPORT_COLUMNS))
        data_frame = _section_data_frame(parsed, section, description, data_date, df, result)
        if not data_frame.empty:
            frames.append(data_frame)

    for warning in parsed.analysis_warnings:
        frames.append(
            pd.DataFrame(
                [
                    {
                        "section": "Data Quality Warnings",
                        "record_type": "warning",
                        "row_meaning": "Warning that must be considered before analysis",
                        "stock_code": parsed.stock_code,
                        "stock_name": parsed.stock_name,
                        "webbsite_issue_id": parsed.issue_id,
                        "fetched_time": parsed.fetched_time,
                        "data_as_of_trading_date": _data_as_of_trading_date(parsed),
                        "fetch_status": "warning",
                        "error_message": warning,
                        "section_row_count": len(parsed.analysis_warnings),
                        "data_quality_status": "warning",
                        "data_quality_warning": warning,
                    }
                ]
            ).reindex(columns=COMMON_EXPORT_COLUMNS)
        )

    frames.extend(extras_frames(parsed, extras))
    output = pd.concat(frames, ignore_index=True, sort=False)

    preferred_data_columns = [
        "Field",
        "Value",
        "Date",
        "Rank",
        "Participant",
        "CCASS ID",
        "Holding",
        "Stake %",
        "Cumulative %",
        "Change",
        "Change in shares",
        "Change %",
        "Holding after",
        "Stake after",
        "Top 5 %",
        "Top 10 %",
        "Top 10 + NCIP %",
        "Stake in CCASS %",
        "Close",
        "Open",
        "High",
        "Low",
        "Volume",
        "Turnover",
        "VWAP",
        "price_source",
        "vwap_est",
    ]
    ordered = (
        COMMON_EXPORT_COLUMNS
        + [column for column in preferred_data_columns if column in output.columns]
        + [
            column
            for column in output.columns
            if column not in COMMON_EXPORT_COLUMNS and column not in preferred_data_columns
        ]
    )
    return output.reindex(columns=ordered).to_csv(index=False).encode("utf-8-sig")


def raw_preview_dataframe(results: dict[str, FetchResult]) -> pd.DataFrame:
    rows = []
    for record in table_preview_records(results):
        rows.append(
            {
                "section": record["section"],
                "table_index": record["table_index"],
                "shape": record["shape"],
                "columns": ", ".join(record["columns"]),
                "preview": str(record["preview"]),
            }
        )
    return pd.DataFrame(rows)


def excel_bytes(parsed: ParsedCCASS, results: dict[str, FetchResult], extras: dict | None = None) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame([metadata_dict(parsed)]).to_excel(writer, sheet_name="metadata", index=False)
        summary = build_fetch_summary(parsed, results)
        summary[
            ["Section", "Status", "Tables found", "Selected table index", "Latest date / data date", "Error"]
        ].to_excel(writer, sheet_name="fetch_summary", index=False)
        summary[["Section", "URL"]].to_excel(writer, sheet_name="source_urls", index=False)
        parsed.holdings_table.to_excel(writer, sheet_name="holdings", index=False)
        parsed.changes_table.to_excel(writer, sheet_name="changes", index=False)
        parsed.big_changes_table.to_excel(writer, sheet_name="bigchanges", index=False)
        _annotate_data_quality("Concentration", parsed.concentration_table).to_excel(
            writer, sheet_name="concentration", index=False
        )
        parsed.price_history_table.to_excel(writer, sheet_name="price_history", index=False)
        if parsed.analysis_warnings:
            pd.DataFrame(
                {"warning": parsed.analysis_warnings}
            ).to_excel(writer, sheet_name="data_quality_warnings", index=False)
        extra_sheets = [
            ("hkex_announcements", "hkex_announcements"),
            ("events", "events"),
            ("share_capital", "share_changes"),
            ("buybacks", "buybacks"),
            ("managers_f10", "managers_f10"),
        ]
        for sheet_name, key in extra_sheets:
            records = (extras or {}).get(key) or []
            if records:
                pd.DataFrame(records).to_excel(writer, sheet_name=sheet_name, index=False)
        raw_preview_dataframe(results).to_excel(writer, sheet_name="raw_table_previews", index=False)
    return buffer.getvalue()

