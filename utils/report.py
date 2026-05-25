from __future__ import annotations

import pandas as pd

from .fetcher import FetchResult
from .parser import ParsedCCASS


REPORT_COLUMNS = {
    "holdings": ["Rank", "Participant", "CCASS ID", "Holding", "Stake %", "Cumulative %"],
    "changes": ["Participant", "Change", "Change %", "Holding after", "Stake after"],
    "big_changes": ["Date", "Participant", "Change in shares", "Change %"],
    "concentration": ["Date", "Top 5", "Top 10", "Stake in CCASS"],
}


def ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = df.copy() if df is not None and not df.empty else pd.DataFrame(columns=columns)
    for col in columns:
        if col not in out.columns:
            out[col] = ""
    return out[columns]


def markdown_table(df: pd.DataFrame, columns: list[str]) -> str:
    out = ensure_columns(df, columns)
    return out.to_markdown(index=False)


def build_source_url_list(results: dict[str, FetchResult]) -> str:
    lines = []
    for name, result in results.items():
        final_url = result.final_url or result.url
        status = f" status={result.status}" if result.status else ""
        ok = "ok" if result.ok else "failed"
        lines.append(f"* {name}: {final_url} ({ok}{status})")
    return "\n".join(lines)


def build_error_section(parsed: ParsedCCASS) -> str:
    if not parsed.page_errors:
        return ""
    lines = ["\n## Fetch Errors\n"]
    for error in parsed.page_errors:
        lines.extend(
            [
                f"* Page: {error['page']}",
                f"* failed URL: {error['failed_url']}",
                f"* error type: {error['error_type']}",
                f"* error message: {error['error_message']}",
                "",
            ]
        )
    return "\n".join(lines)


def build_report(parsed: ParsedCCASS, results: dict[str, FetchResult]) -> str:
    title_code = parsed.stock_code or "Unknown stock code"
    title_name = parsed.stock_name or "Unknown stock name"
    source_urls = build_source_url_list(results)

    report = f"""# {title_code} {title_name}｜Webb-site CCASS 抽取結果

## Metadata

* Stock code: {parsed.stock_code}
* Stock name: {parsed.stock_name}
* Webb-site issue ID: {parsed.issue_id}
* Fetched time: {parsed.fetched_time}
* Source URLs:
{source_urls}
* CCASS data date: {parsed.ccass_data_date}

## Holdings

{markdown_table(parsed.holdings_table, REPORT_COLUMNS["holdings"])}

## Changes

* Date range: {parsed.date_range}
* Trading date: {parsed.trading_date}
* Volume: {parsed.volume}
* Turnover: {parsed.turnover}
* Average price: {parsed.average_price}
* Total CCASS change: {parsed.total_ccass_change}

{markdown_table(parsed.changes_table, REPORT_COLUMNS["changes"])}

## Big Changes

{markdown_table(parsed.big_changes_table, REPORT_COLUMNS["big_changes"])}
"""
    if parsed.transfer_flags:
        report += "\nPossible same-day transfer flags:\n"
        for flag in parsed.transfer_flags:
            report += f"\n* {flag}"
        report += "\n"

    report += f"""
## Concentration

{markdown_table(parsed.concentration_table, REPORT_COLUMNS["concentration"])}

## Notes for ChatGPT Analysis

* 單一券商減倉不等於派貨，必須對照成交量
* 同日一增一減且總量不變，優先考慮轉倉
* Top 5 / Top 10 上升代表貨源集中
* Top 5 / Top 10 下降 + 散戶券商增加 + 成交量足夠，才可考慮派貨風險
* CCASS 是 T+2 數據，必須以頁面顯示日期為準
* 本工具只作公開資料整理及研究用途，不構成投資建議
"""
    report += build_error_section(parsed)
    return report
