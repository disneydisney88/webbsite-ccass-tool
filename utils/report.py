from __future__ import annotations

import pandas as pd

from .fetcher import FetchResult
from .parser import ParsedCCASS, build_fetch_summary


REPORT_COLUMNS = {
    "holdings": ["Rank", "Participant", "CCASS ID", "Holding", "Stake %", "Cumulative %"],
    "changes": ["Participant", "Change", "Change %", "Holding after", "Stake after"],
    "concentration": ["Date", "Top 5 %", "Top 10 %", "Top 10 + NCIP %", "Stake in CCASS %"],
    "price_history": ["Date", "Close", "Open", "High", "Low", "Volume", "Turnover", "VWAP"],
}


def ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = df.copy() if df is not None and not df.empty else pd.DataFrame(columns=columns)
    for col in columns:
        if col not in out.columns:
            out[col] = ""
    return out[columns]


def markdown_table(df: pd.DataFrame, columns: list[str] | None = None) -> str:
    out = ensure_columns(df, columns) if columns else df.copy()
    if out.empty:
        out = pd.DataFrame(columns=columns or [])
    return out.to_markdown(index=False)


def source_url_lines(results: dict[str, FetchResult]) -> str:
    return "\n".join(f"* {name}: {result.final_url or result.url}" for name, result in results.items())


def value_or_reason(value: str, failed: bool, label: str) -> str:
    if value:
        return value
    if failed:
        return f"not available because {label} table parsing failed"
    return "not available"


def bullet_list(items: list[str]) -> str:
    if not items:
        return "* not available"
    return "\n".join(f"* {item}" for item in items)


def data_quality_warnings(parsed: ParsedCCASS, results: dict[str, FetchResult]) -> list[str]:
    warnings = list(parsed.analysis_warnings)
    for section, result in results.items():
        if result and not result.ok:
            warnings.append(f"{section} failed: {result.error_type} - {result.error_message}")
    for name, section_parse in parsed.section_parses.items():
        if section_parse.status in {"failed", "no matching table", "partial success", "manually selected"} and section_parse.error:
            warnings.append(f"{name}: {section_parse.status} - {section_parse.error}")
        elif section_parse.status == "manually selected":
            warnings.append(f"{name}: manually selected table {section_parse.selected_table_index}")
    if parsed.id_lookup_method in {"known mapping fallback", "manually entered"}:
        warnings.append(f"ID lookup used {parsed.id_lookup_method}.")
    return list(dict.fromkeys(warnings))


def concentration_change_lines(parsed: ParsedCCASS) -> str:
    if not parsed.concentration_5day_change:
        return "* not available"
    labels = {
        "Top 5 %": "Top 5 % change",
        "Top 10 %": "Top 10 % change",
        "Stake in CCASS %": "Stake in CCASS % change",
    }
    return "\n".join(f"* {labels.get(key, key)}: {value}" for key, value in parsed.concentration_5day_change.items())


def extras_report_sections(extras: dict | None) -> str:
    """Markdown sections for the newer data sources (events / capital / managers)."""
    extras = extras or {}
    parts: list[str] = []

    events = extras.get("events") or []
    parts.append("## Corporate Events (Webb-site 權益事件)\n")
    if events:
        keep = ["announced", "type", "new_old", "ex_date", "amount", "year_end", "notes"]
        df = pd.DataFrame(events)
        parts.append(markdown_table(df[[c for c in keep if c in df.columns]]))
    else:
        parts.append("* not available")

    share_changes = extras.get("share_changes") or []
    parts.append("\n## Share Capital Changes (股本變化,10jqka)\n")
    if share_changes:
        keep = ["announce_date", "shares_million", "reason", "reason_tags", "change_date"]
        df = pd.DataFrame(share_changes)
        if "reason_tags" in df.columns:
            df = df.assign(reason_tags=df["reason_tags"].apply(lambda tags: ",".join(tags) if isinstance(tags, list) else tags))
        parts.append(markdown_table(df[[c for c in keep if c in df.columns]]))
    else:
        parts.append("* not available")

    buybacks = extras.get("buybacks") or []
    parts.append("\n## Buybacks (股份回購,10jqka)\n")
    if buybacks:
        parts.append(f"* Total buyback records: {len(buybacks)} (most recent 20 shown)\n")
        parts.append(markdown_table(pd.DataFrame(buybacks[:20])))
    else:
        parts.append("* not available")

    managers = extras.get("managers_f10") or []
    parts.append("\n## Current Management (現任高管,10jqka)\n")
    if managers:
        keep = ["name", "positions", "tenure_from", "tenure_to", "sex", "age", "education", "salary"]
        df = pd.DataFrame(managers)
        parts.append(markdown_table(df[[c for c in keep if c in df.columns]]))
        parts.append("\n### 高管背景簡介\n")
        for manager in managers:
            if manager.get("biography"):
                parts.append(f"* **{manager.get('name', '-')}**({manager.get('positions', '-')}): {manager['biography']}")
    else:
        parts.append("* not available")

    return "\n".join(parts) + "\n\n"


def build_report(parsed: ParsedCCASS, results: dict[str, FetchResult], hkex_announcements=None, extras: dict | None = None) -> str:
    fetch_summary = build_fetch_summary(parsed, results)
    fetch_summary_report = fetch_summary[
        ["Section", "Status", "Tables found", "Selected table index", "Latest date / data date", "Error"]
    ].rename(columns={"Selected table index": "Selected table", "Latest date / data date": "Latest date"})
    warnings = data_quality_warnings(parsed, results)

    holdings_failed = parsed.holdings_table.empty
    changes_failed = parsed.changes_table.empty
    big_failed = parsed.big_changes_table.empty
    conc_failed = parsed.concentration_table.empty
    price_failed = parsed.price_history_table.empty

    report = f"""# {parsed.stock_code or "Unknown stock code"} {parsed.stock_name or "Unknown stock name"}｜Webb-site CCASS 抽取結果

## AI Analysis Ready Summary

* Stock code: {parsed.stock_code}
* Stock name: {parsed.stock_name}
* Webb-site issue ID: {parsed.issue_id}
* Holdings latest date: {value_or_reason(parsed.holdings_data_date, holdings_failed, "Holdings")}
* Changes trading date: {value_or_reason(parsed.changes_trading_date, changes_failed, "Changes")}
* Total in CCASS %: {parsed.total_in_ccass_pct}
* Top 5 %: {parsed.top5_cumulative_pct}
* Top 10 %: {parsed.top10_cumulative_pct}
* Largest participant: {parsed.largest_participant}
* Major increases:
{bullet_list(parsed.major_increases)}
* Major decreases:
{bullet_list(parsed.major_decreases)}
* Big Changes latest date: {value_or_reason(parsed.big_changes_latest_date, big_failed, "Big Changes")}
* Concentration latest date: {value_or_reason(parsed.concentration_latest_date, conc_failed, "Concentration")}
* Price latest date: {value_or_reason(parsed.price_history_latest_date, price_failed, "Price History")}
* Latest close / volume / turnover: {parsed.latest_price or "not available"} / {parsed.latest_price_volume or "not available"} / {parsed.latest_price_turnover or "not available"}

## Fetch Summary

{markdown_table(fetch_summary_report)}

## Metadata

* Stock code: {parsed.stock_code}
* Stock name: {parsed.stock_name}
* Webb-site issue ID: {parsed.issue_id}
* ID lookup method: {parsed.id_lookup_method}
* Fetched time: {parsed.fetched_time}
* Holdings data date: {value_or_reason(parsed.holdings_data_date, holdings_failed, "Holdings")}
* Changes date range: {value_or_reason(parsed.changes_date_range, changes_failed, "Changes")}
* Changes trading date: {value_or_reason(parsed.changes_trading_date, changes_failed, "Changes")}
* Big Changes latest date: {value_or_reason(parsed.big_changes_latest_date, big_failed, "Big Changes")}
* Concentration latest date: {value_or_reason(parsed.concentration_latest_date, conc_failed, "Concentration")}
* Price latest date: {value_or_reason(parsed.price_history_latest_date, price_failed, "Price History")}
* Latest close: {parsed.latest_price}
* Latest volume: {parsed.latest_price_volume}
* Latest turnover: {parsed.latest_price_turnover}
* Latest VWAP: {parsed.latest_price_vwap}
* Source URLs:
{source_url_lines(results)}
* HKEX announcements period: {getattr(hkex_announcements, "from_date", "")} to {getattr(hkex_announcements, "to_date", "")}
* HKEX announcements total count: {getattr(hkex_announcements, "total_count", 0)}

## HKEX Announcements

{markdown_table(hkex_announcements.table) if hkex_announcements is not None and hkex_announcements.table is not None and not hkex_announcements.table.empty else "No HKEX announcements found or fetched."}

## Holdings

{markdown_table(parsed.holdings_table, REPORT_COLUMNS["holdings"])}

## Holdings Summary

* Issued securities: {parsed.issued_securities}
* Total in CCASS: {parsed.total_in_ccass}
* Total in CCASS %: {parsed.total_in_ccass_pct}
* Securities not in CCASS: {parsed.securities_not_in_ccass}
* Largest participant: {parsed.largest_participant}
* Top 5: {parsed.top5_cumulative_pct}
* Top 10: {parsed.top10_cumulative_pct}

## Changes

* Date range: {parsed.changes_date_range}
* Trading date: {parsed.changes_trading_date}
* Volume: {parsed.volume}
* Turnover: {parsed.turnover}
* Average price: {parsed.average_price}
* Total CCASS change: {parsed.total_ccass_change}

{markdown_table(parsed.changes_table, REPORT_COLUMNS["changes"])}

## Changes Auto Flags

{bullet_list(parsed.changes_flags)}

## Big Changes

{markdown_table(parsed.big_changes_table)}

"""
    if parsed.transfer_flags:
        report += "Possible large custody transfer / warehouse transfer flags:\n"
        report += bullet_list(parsed.transfer_flags) + "\n\n"

    report += extras_report_sections(extras)

    report += f"""## Concentration

{markdown_table(parsed.concentration_table, REPORT_COLUMNS["concentration"])}

## Concentration Recent 5 Trading Days Change

{concentration_change_lines(parsed)}

## Price History

{markdown_table(parsed.price_history_table.head(80), REPORT_COLUMNS["price_history"])}

## Data Quality Warnings

"""
    report += bullet_list(warnings) if warnings else "* No abnormal values or failed sections detected by this tool."

    report += """

## Notes for ChatGPT Analysis

* 單一券商減倉不等於派貨，必須對照成交量。
* 同日一增一減且總量不變，優先考慮轉倉。
* Top 5 / Top 10 上升代表貨源集中。
* Top 5 / Top 10 下降 + 散戶券商增加 + 成交量足夠，才可考慮派貨風險。
* CCASS 是 T+2 數據，必須以頁面顯示日期為準。
* 如果 Holdings 或 Changes 抽取失敗，不可聲稱已完成完整 CCASS 分析。

## Copy to ChatGPT Analysis Prompt

請根據以上 Webb-site CCASS 抽取結果，分開以下部分分析，並嚴格區分事實與推理：

【已查證事實】
只列出表格直接顯示的 stock code、issue ID、日期、持倉、變動、集中度和資料來源。

【CCASS 觀察】
總結 Holdings、Changes、Big Changes 和 Concentration 各自反映的重點。

【集中度變化】
分析 Top 5、Top 10、Stake in CCASS 的近期變化，尤其是最近 5 個交易日變化。

【收貨 / 轉倉 / 派貨推理】
基於券商增減、成交量、同日一增一減、集中度升跌作審慎推理；不要把單一券商減倉直接等同派貨。

【需要再核實事項】
列出需要用成交量、股價、公告、配售、供股、解禁、公司行動或更多 CCASS 日期再確認的事項。
"""
    return report
