from __future__ import annotations

from io import BytesIO

import pandas as pd

from .fetcher import FetchResult
from .parser import ParsedCCASS


def parsed_to_json_ready(parsed: ParsedCCASS, results: dict[str, FetchResult]) -> dict:
    return {
        "metadata": {
            "stock_code": parsed.stock_code,
            "stock_name": parsed.stock_name,
            "issue_id": parsed.issue_id,
            "fetched_time": parsed.fetched_time,
            "ccass_data_date": parsed.ccass_data_date,
            "issued_securities": parsed.issued_securities,
            "total_in_ccass": parsed.total_in_ccass,
            "total_in_ccass_pct": parsed.total_in_ccass_pct,
            "securities_not_in_ccass": parsed.securities_not_in_ccass,
            "top5_cumulative_pct": parsed.top5_cumulative_pct,
            "top10_cumulative_pct": parsed.top10_cumulative_pct,
            "largest_participant": parsed.largest_participant,
        },
        "fetch_log": [result.to_log() for result in results.values()],
        "holdings": parsed.holdings_table.to_dict(orient="records"),
        "changes": parsed.changes_table.to_dict(orient="records"),
        "big_changes": parsed.big_changes_table.to_dict(orient="records"),
        "concentration": parsed.concentration_table.to_dict(orient="records"),
        "transfer_flags": parsed.transfer_flags,
        "page_errors": parsed.page_errors,
    }


def combined_csv(parsed: ParsedCCASS) -> bytes:
    frames = {
        "Holdings": parsed.holdings_table,
        "Changes": parsed.changes_table,
        "Big Changes": parsed.big_changes_table,
        "Concentration": parsed.concentration_table,
    }
    parts = []
    for name, df in frames.items():
        if df.empty:
            continue
        section = df.copy()
        section.insert(0, "Section", name)
        parts.append(section)
    if not parts:
        return b""
    return pd.concat(parts, ignore_index=True, sort=False).to_csv(index=False).encode("utf-8-sig")


def excel_bytes(parsed: ParsedCCASS, results: dict[str, FetchResult]) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        metadata = parsed_to_json_ready(parsed, results)["metadata"]
        pd.DataFrame([metadata]).to_excel(writer, sheet_name="Metadata", index=False)
        pd.DataFrame([result.to_log() for result in results.values()]).to_excel(writer, sheet_name="Fetch Log", index=False)
        parsed.holdings_table.to_excel(writer, sheet_name="Holdings", index=False)
        parsed.changes_table.to_excel(writer, sheet_name="Changes", index=False)
        parsed.big_changes_table.to_excel(writer, sheet_name="Big Changes", index=False)
        parsed.concentration_table.to_excel(writer, sheet_name="Concentration", index=False)
    return buffer.getvalue()
