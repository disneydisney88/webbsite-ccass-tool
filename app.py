from __future__ import annotations

import json
import os

import streamlit as st

from utils.exporters import combined_csv, excel_bytes, parsed_to_json_ready
from utils.fetcher import clean_stock_code, fetch_all, looks_like_issue_id, resolve_issue_id_from_stock
from utils.parser import parse_results
from utils.report import build_report


st.set_page_config(page_title="Webb-site CCASS Extractor", layout="wide")


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y"}


def render_fetch_logs(logs: list[dict]) -> None:
    for log in logs:
        icon = "✅" if log["ok"] else "⚠️"
        with st.expander(f"{icon} {log['page']} - {log['method']} - tables: {log['table_count']}", expanded=not log["ok"]):
            st.json(log)


def render_page_tab(name: str, result) -> None:
    if not result:
        st.info("No result.")
        return
    st.caption(result.final_url or result.url)
    if result.ok:
        st.success(f"Fetched by {result.method}; extracted {len(result.tables)} table(s).")
    else:
        st.warning(f"Failed: {result.error_type} - {result.error_message}")
    if result.raw_text:
        st.text_area("Raw text preview", result.raw_text[:4000], height=180, key=f"raw_{name}")
    for idx, table in enumerate(result.tables, start=1):
        st.subheader(f"Table {idx}")
        st.dataframe(table, use_container_width=True)


st.title("Webb-site CCASS 財技資料抽取工具")
st.caption("公開資料整理及研究用途，不構成投資建議。請避免高頻抓取。")

with st.sidebar:
    st.header("Input")
    mode = st.radio("Input type", ["Auto detect", "Stock code", "Issue ID"], index=0)
    user_input = st.text_input("Stock code / issue ID", placeholder="例如 06080 或 25298")
    manual_issue_id = st.text_input("Manual issue ID fallback", placeholder="自動找不到時可填入")
    timeout = st.number_input("Timeout per page seconds", min_value=10, max_value=120, value=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "60")), step=5)
    headless = st.toggle("Playwright headless", value=env_bool("PLAYWRIGHT_HEADLESS", True))
    fetch_clicked = st.button("Fetch Webb-site Data", type="primary", use_container_width=True)

if "results" not in st.session_state:
    st.session_state.results = None
    st.session_state.parsed = None
    st.session_state.report = ""
    st.session_state.issue_id = ""
    st.session_state.resolve_result = None

if fetch_clicked:
    value = user_input.strip()
    if not value and not manual_issue_id.strip():
        st.error("請輸入股票代號或 issue ID。")
    else:
        issue_id = ""
        stock_code = ""
        resolve_result = None

        with st.status("Resolving issue ID...", expanded=True) as status:
            if mode == "Issue ID" or (mode == "Auto detect" and looks_like_issue_id(value)):
                issue_id = value
                st.write(f"Using issue ID: {issue_id}")
            else:
                stock_code = clean_stock_code(value)
                st.write(f"Trying orgdata lookup for stock code: {stock_code}")
                issue_id, resolve_result = resolve_issue_id_from_stock(stock_code, timeout=int(timeout), headless=headless)
                if issue_id:
                    st.write(f"Found issue ID: {issue_id}")
                elif manual_issue_id.strip():
                    issue_id = manual_issue_id.strip()
                    st.write(f"Using manual issue ID: {issue_id}")
                else:
                    st.write("Issue ID not found. Please provide manual issue ID.")
            status.update(label="Issue ID resolution complete", state="complete")

        if issue_id:
            with st.status("Fetching Webb-site pages...", expanded=True) as status:
                results = fetch_all(issue_id, timeout=int(timeout), headless=headless)
                for result in results.values():
                    ok_text = "success" if result.ok else "failed"
                    st.write(f"{result.name}: {ok_text}, tables={len(result.tables)}")
                status.update(label="Fetch complete", state="complete")

            parsed = parse_results(issue_id, results, stock_code=stock_code)
            report = build_report(parsed, results)
            st.session_state.results = results
            st.session_state.parsed = parsed
            st.session_state.report = report
            st.session_state.issue_id = issue_id
            st.session_state.resolve_result = resolve_result

if st.session_state.results:
    parsed = st.session_state.parsed
    results = st.session_state.results
    report = st.session_state.report
    json_ready = parsed_to_json_ready(parsed, results)

    st.subheader("抓取狀態")
    if st.session_state.resolve_result:
        render_fetch_logs([st.session_state.resolve_result.to_log()])
    render_fetch_logs([result.to_log() for result in results.values()])

    st.subheader("Summary")
    cols = st.columns(4)
    cols[0].metric("Issue ID", parsed.issue_id)
    cols[1].metric("CCASS date", parsed.ccass_data_date or "-")
    cols[2].metric("Top 5", parsed.top5_cumulative_pct or "-")
    cols[3].metric("Top 10", parsed.top10_cumulative_pct or "-")

    tab_holdings, tab_changes, tab_big, tab_conc, tab_report = st.tabs(
        ["Holdings", "Changes", "Big Changes", "Concentration", "Markdown report"]
    )
    with tab_holdings:
        render_page_tab("Holdings", results.get("Holdings"))
    with tab_changes:
        render_page_tab("Changes", results.get("Changes"))
    with tab_big:
        render_page_tab("Big Changes", results.get("Big Changes"))
        if parsed.transfer_flags:
            st.warning("\n".join(parsed.transfer_flags))
    with tab_conc:
        render_page_tab("Concentration", results.get("Concentration"))
        if results.get("Concentration") and not results["Concentration"].ok and not parsed.concentration_table.empty:
            st.info("Concentration page failed. Showing fallback Top 5 / Top 10 from Holdings cumulative stake.")
            st.dataframe(parsed.concentration_table, use_container_width=True)
    with tab_report:
        st.text_area("Markdown report", report, height=520)
        st.text_area("Copy for ChatGPT", report, height=260)

    st.subheader("Downloads")
    base_name = f"ccass_{parsed.stock_code or parsed.issue_id}"
    col1, col2, col3, col4 = st.columns(4)
    col1.download_button("Markdown", data=report.encode("utf-8"), file_name=f"{base_name}.md", mime="text/markdown")
    col2.download_button("CSV", data=combined_csv(parsed), file_name=f"{base_name}.csv", mime="text/csv")
    col3.download_button(
        "Excel",
        data=excel_bytes(parsed, results),
        file_name=f"{base_name}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    col4.download_button(
        "JSON",
        data=json.dumps(json_ready, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name=f"{base_name}.json",
        mime="application/json",
    )
else:
    st.info("輸入股票代號或 Webb-site issue ID 後按 Fetch Webb-site Data。")
