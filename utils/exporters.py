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
        "fetched_time": parsed.fetched_time,
        "holdings_data_date": parsed.holdings_data_date,
        "changes_date_range": parsed.changes_date_range,
        "changes_trading_date": parsed.changes_trading_date,
        "big_changes_latest_date": parsed.big_changes_latest_date,
        "concentration_latest_date": parsed.concentration_latest_date,
        "price_history_latest_date": parsed.price_history_latest_date,
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


def combined_stock_csv(parsed: ParsedCCASS, results: dict[str, FetchResult]) -> bytes:
    sections = [
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
            "Historical close price, volume, turnover and VWAP",
            parsed.price_history_latest_date,
            parsed.price_history_table,
        ),
    ]
    frames = []
    for section, description, data_date, df in sections:
        if df is None or df.empty:
            continue
        result = results.get(section)
        out = df.copy()
        out.insert(0, "section", section)
        out.insert(1, "row_meaning", description)
        out.insert(2, "stock_code", parsed.stock_code)
        out.insert(3, "stock_name", parsed.stock_name)
        out.insert(4, "webbsite_issue_id", parsed.issue_id)
        out.insert(5, "fetched_time", parsed.fetched_time)
        out.insert(6, "data_date_or_latest_date", data_date)
        out.insert(7, "source_url", result.final_url or result.url if result else "")
        frames.append(out)
    if not frames:
        return pd.DataFrame(
            [
                {
                    "section": "No parsed data",
                    "row_meaning": "No parsed tables were available",
                    "stock_code": parsed.stock_code,
                    "stock_name": parsed.stock_name,
                    "webbsite_issue_id": parsed.issue_id,
                    "fetched_time": parsed.fetched_time,
                }
            ]
        ).to_csv(index=False).encode("utf-8-sig")
    return pd.concat(frames, ignore_index=True, sort=False).to_csv(index=False).encode("utf-8-sig")


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


def excel_bytes(parsed: ParsedCCASS, results: dict[str, FetchResult]) -> bytes:
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
        raw_preview_dataframe(results).to_excel(writer, sheet_name="raw_table_previews", index=False)
    return buffer.getvalue()
