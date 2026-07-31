from __future__ import annotations

from io import BytesIO

import pandas as pd

from .fetcher import FetchResult
from .parser import ParsedCCASS, build_fetch_summary, table_preview_records


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


def parsed_to_json_ready(parsed: ParsedCCASS, results: dict[str, FetchResult]) -> dict:
    return {
        "metadata": metadata_dict(parsed),
        "fetch_summary": build_fetch_summary(parsed, results).to_dict(orient="records"),
        "fetch_log": [result.to_log() for result in results.values()],
        "holdings": parsed.holdings_table.to_dict(orient="records"),
        "changes": parsed.changes_table.to_dict(orient="records"),
        "bigchanges": parsed.big_changes_table.to_dict(orient="records"),
        "concentration": parsed.concentration_table.to_dict(orient="records"),
        "price_history": parsed.price_history_table.to_dict(orient="records"),
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
    ("Corporate Events", "Webb-site entitlement events (dividends, splits, rights)", "events", "events_url"),
    ("Share Capital Changes", "Issued-share history: placements, option exercises, buyback cancellations (10jqka F10)", "share_changes", "equity_url"),
    ("Buybacks", "Per-day share buyback records (10jqka F10)", "buybacks", "equity_url"),
    ("Managers F10", "Current management with tenure, salary and biography (10jqka F10)", "managers_f10", "managers_url"),
]


def extras_frames(parsed: ParsedCCASS, extras: dict | None) -> list[pd.DataFrame]:
    frames = []
    for section, description, key, url_key in EXTRA_SECTIONS:
        records = (extras or {}).get(key) or []
        if not records:
            continue
        out = pd.DataFrame(records)
        out.insert(0, "section", section)
        out.insert(1, "record_type", "data")
        out.insert(2, "row_meaning", description)
        out.insert(3, "stock_code", parsed.stock_code)
        out.insert(4, "stock_name", parsed.stock_name)
        out.insert(5, "webbsite_issue_id", parsed.issue_id)
        out.insert(6, "fetched_time", parsed.fetched_time)
        out.insert(7, "data_date_or_latest_date", "")
        out.insert(8, "source_url", (extras or {}).get(url_key, ""))
        out.insert(9, "fetch_status", "success")
        out.insert(10, "fetch_method", "supplementary_source")
        frames.append(out)
    return frames


def combined_stock_csv(parsed: ParsedCCASS, results: dict[str, FetchResult], extras: dict | None = None) -> bytes:
    """Create one self-describing CSV for a stock.

    This is deliberately not merely a concatenation of successfully parsed
    tables.  A downloaded file must remain useful to an API/MCP/AI workflow
    when a source page is temporarily unavailable, so every section gets a
    status record with its source and failure detail before any data rows.
    """
    company_result = results.get("Company / orgdata")
    company_table = company_result.tables[0] if company_result and company_result.tables else pd.DataFrame()
    sections = [
        (
            "Company / orgdata",
            "Company identity and stock metadata used to resolve the Webb-site issue ID",
            "",
            company_table,
        ),
        (
            "Holdings",
            "Broker/participant holdings on the Holdings data date",
            parsed.holdings_data_date,
            parsed.holdings_table,
        ),
        (
            "Changes",
            "Daily CCASS participant holding changes",
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
            "Historical close price, volume, turnover/VWAP. Yahoo fallback turnover is estimated as volume x close.",
            parsed.price_history_latest_date,
            parsed.price_history_table,
        ),
    ]
    base_columns = [
        "section", "record_type", "row_meaning", "stock_code", "stock_name",
        "webbsite_issue_id", "fetched_time", "data_date_or_latest_date",
        "source_url", "fetch_status", "fetch_method", "http_status",
        "error_type", "error_message", "fallback_method_used",
    ]
    frames = [
        pd.DataFrame(
            [
                {
                    "section": "Metadata",
                    "record_type": "metadata",
                    "row_meaning": "File-level source and identity metadata for this export",
                    "stock_code": parsed.stock_code,
                    "stock_name": parsed.stock_name,
                    "webbsite_issue_id": parsed.issue_id,
                    "fetched_time": parsed.fetched_time,
                    "source": parsed.source,
                    "mirror_status": parsed.mirror_status,
                    "mirror_base_url": parsed.mirror_base_url,
                    "history_depth_days": parsed.history_depth_days,
                    "db_restored_from_backup": parsed.db_restored_from_backup,
                }
            ]
        )
    ]
    for section, description, data_date, df in sections:
        if df is None or df.empty:
            df = pd.DataFrame()
        result = results.get(section)
        status_row = {
            "section": section,
            "record_type": "fetch_status",
            "row_meaning": description,
            "stock_code": parsed.stock_code,
            "stock_name": parsed.stock_name,
            "webbsite_issue_id": parsed.issue_id,
            "fetched_time": parsed.fetched_time,
            "data_date_or_latest_date": data_date,
            "source_url": (result.final_url or result.url) if result else "",
            "fetch_status": "success" if result and result.ok else "failed_or_no_parsed_table",
            "fetch_method": result.method if result else "not_fetched",
            "http_status": result.status if result else "",
            "error_type": result.error_type if result else "NO_RESULT",
            "error_message": result.error_message if result else "No fetch result was returned for this section.",
            "fallback_method_used": result.fallback_method_used if result else "",
        }
        frames.append(pd.DataFrame([status_row]))
        if df.empty:
            continue
        out = df.copy()
        out.insert(0, "section", section)
        out.insert(1, "record_type", "data")
        out.insert(2, "row_meaning", description)
        out.insert(3, "stock_code", parsed.stock_code)
        out.insert(4, "stock_name", parsed.stock_name)
        out.insert(5, "webbsite_issue_id", parsed.issue_id)
        out.insert(6, "fetched_time", parsed.fetched_time)
        out.insert(7, "data_date_or_latest_date", data_date)
        out.insert(8, "source_url", result.final_url or result.url if result else "")
        out.insert(9, "fetch_status", "success" if result and result.ok else "parsed_from_available_data")
        out.insert(10, "fetch_method", result.method if result else "")
        out.insert(11, "http_status", result.status if result else "")
        out.insert(12, "error_type", result.error_type if result else "")
        out.insert(13, "error_message", result.error_message if result else "")
        out.insert(14, "fallback_method_used", result.fallback_method_used if result else "")
        frames.append(out)
    for warning in parsed.analysis_warnings:
        frames.append(
            pd.DataFrame(
                [{
                    "section": "Data Quality Warnings",
                    "record_type": "warning",
                    "row_meaning": "Warning that must be considered before analysis",
                    "stock_code": parsed.stock_code,
                    "stock_name": parsed.stock_name,
                    "webbsite_issue_id": parsed.issue_id,
                    "fetched_time": parsed.fetched_time,
                    "error_message": warning,
                }]
            )
        )
    frames.extend(extras_frames(parsed, extras))
    output = pd.concat(frames, ignore_index=True, sort=False)
    ordered = base_columns + [column for column in output.columns if column not in base_columns]
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
        parsed.concentration_table.to_excel(writer, sheet_name="concentration", index=False)
        parsed.price_history_table.to_excel(writer, sheet_name="price_history", index=False)
        extra_sheets = [
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
