from __future__ import annotations

import json
import os
import importlib

import streamlit as st
import streamlit.components.v1 as components

import utils.exporters as exporters
from utils.fetcher import IssueLookup, clean_stock_code, fetch_all, resolve_issue_id_from_stock
from utils.parser import SECTIONS, build_fetch_summary, parse_results, table_preview_records
from utils.report import build_report

exporters = importlib.reload(exporters)
combined_stock_csv = exporters.combined_stock_csv
excel_bytes = exporters.excel_bytes
parsed_to_json_ready = exporters.parsed_to_json_ready


def csv_bytes(df):
    if hasattr(exporters, "csv_bytes"):
        return exporters.csv_bytes(df)
    return df.to_csv(index=False).encode("utf-8-sig")


st.set_page_config(page_title="Webb-site CCASS Extractor", layout="wide")


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y"}


def empty_lookup() -> IssueLookup:
    return IssueLookup(stock_code="", issue_id="", method="", status="", message="")


def table_options(result) -> list[str]:
    options = ["Auto select"]
    if not result:
        return options
    for idx, table in enumerate(result.tables, start=1):
        options.append(f"Table {idx} ({table.shape[0]} x {table.shape[1]})")
    return options


def option_to_index(option: str) -> int | None:
    if option == "Auto select":
        return None
    try:
        return int(option.split()[1]) - 1
    except (IndexError, ValueError):
        return None


def render_table_previews(section: str, result) -> None:
    if not result:
        st.info("No fetch result for this section.")
        return
    if result.raw_text:
        with st.expander("Raw text preview", expanded=False):
            st.text_area(f"{section} raw text", result.raw_text[:8000], height=220, key=f"raw_text_{section}")
    for idx, table in enumerate(result.tables, start=1):
        df = table.copy()
        with st.expander(f"{section} raw table {idx} | shape {df.shape[0]} x {df.shape[1]}", expanded=False):
            st.caption("Columns: " + ", ".join(map(str, df.columns)))
            st.dataframe(df.head(3), use_container_width=True)


def render_section(section: str, result, selected_index, parsed_table, summary_text: str = "") -> None:
    st.subheader(section)
    if result:
        st.caption(result.final_url or result.url)
        status = "success" if result.ok else "failed"
        st.write(f"Fetch status: {status} | tables found: {len(result.tables)} | selected table: {selected_index or 'none'}")
        if result.error_message:
            st.warning(f"{result.error_type}: {result.error_message}")
    if summary_text:
        st.info(summary_text)
    if parsed_table is not None and not parsed_table.empty:
        st.dataframe(parsed_table, use_container_width=True)
    else:
        st.warning(f"{section} table parsing failed. Raw table previews are shown below.")
    render_table_previews(section, result)


def get_download_base(parsed) -> str:
    return parsed.stock_code or parsed.issue_id or "ccass"


def render_download_buttons(parsed, results, report: str, key_prefix: str) -> None:
    base = get_download_base(parsed)
    all_csv = combined_stock_csv(parsed, results)
    st.caption("The main CSV combines all sections and labels what each row represents.")
    st.download_button(
        "Download All Data CSV",
        all_csv,
        f"{base}_all_ccass_data.csv",
        "text/csv",
        key=f"{key_prefix}_{base}_all_ccass_data_csv",
        use_container_width=True,
    )
    with st.expander("CSV content preview", expanded=False):
        csv_text = all_csv.decode("utf-8-sig", errors="replace")
        preview_lines = "\n".join(csv_text.splitlines()[:80])
        st.text_area("First 80 CSV lines", preview_lines, height=260, key=f"{key_prefix}_{base}_csv_preview")
    col1, col2, col3, col4 = st.columns(4)
    col1.download_button("Holdings CSV", csv_bytes(parsed.holdings_table), f"{base}_holdings.csv", "text/csv", key=f"{key_prefix}_{base}_holdings_csv")
    col2.download_button("Changes CSV", csv_bytes(parsed.changes_table), f"{base}_changes.csv", "text/csv", key=f"{key_prefix}_{base}_changes_csv")
    col3.download_button("Big Changes CSV", csv_bytes(parsed.big_changes_table), f"{base}_bigchanges.csv", "text/csv", key=f"{key_prefix}_{base}_bigchanges_csv")
    col4.download_button("Concentration CSV", csv_bytes(parsed.concentration_table), f"{base}_concentration.csv", "text/csv", key=f"{key_prefix}_{base}_concentration_csv")

    col5, col6, col7 = st.columns(3)
    col5.download_button("Markdown Report", report.encode("utf-8"), f"{base}_report.md", "text/markdown", key=f"{key_prefix}_{base}_report_md")
    col6.download_button(
        "Excel - All Sections",
        excel_bytes(parsed, results),
        f"{base}_all_sections.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"{key_prefix}_{base}_all_sections_xlsx",
    )
    col7.download_button(
        "Raw Tables JSON",
        json.dumps(json_ready, ensure_ascii=False, indent=2).encode("utf-8"),
        f"{base}_raw_tables.json",
        "application/json",
        key=f"{key_prefix}_{base}_raw_tables_json",
    )


def render_all_parsed_tables(parsed) -> None:
    st.subheader("All Parsed Tables")
    st.caption("Each table is parsed from its own Webb-site source page.")
    sections = [
        ("Holdings", parsed.holdings_data_date, parsed.holdings_table),
        ("Changes", parsed.changes_trading_date or parsed.changes_date_range, parsed.changes_table),
        ("Big Changes", parsed.big_changes_latest_date, parsed.big_changes_table),
        ("Concentration", parsed.concentration_latest_date, parsed.concentration_table),
    ]
    for section, date_value, df in sections:
        st.markdown(f"### {section}")
        st.caption(f"Data date / latest date: {date_value or 'not available'}")
        if df is not None and not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.warning(f"{section} parsed table is not available. Check Raw Table Previews.")


def render_concentration_change(parsed) -> None:
    st.markdown("**Recent 5 trading days concentration change**")
    if not parsed.concentration_5day_change:
        st.caption("Not enough concentration history to calculate.")
        return
    st.dataframe(
        [{"Metric": key, "Change": value} for key, value in parsed.concentration_5day_change.items()],
        use_container_width=True,
    )


def compact_fetch_summary(fetch_summary):
    return fetch_summary[
        ["Section", "Status", "Tables found", "Selected table index", "Latest date / data date", "Error"]
    ].rename(columns={"Selected table index": "Selected table", "Latest date / data date": "Latest date"})


def render_copy_report(report: str) -> None:
    st.text_area("Markdown report", report, height=620)
    payload = json.dumps(report)
    components.html(
        f"""
        <button id="copy-report" style="
            width: 100%;
            padding: 0.75rem 1rem;
            border-radius: 0.5rem;
            border: 1px solid #ff4b4b;
            background: #ff4b4b;
            color: white;
            font-weight: 700;
            cursor: pointer;
        ">Copy report to clipboard</button>
        <div id="copy-status" style="margin-top: 0.5rem; color: #9ca3af; font-family: sans-serif;"></div>
        <script>
        const report = {payload};
        const button = document.getElementById("copy-report");
        const status = document.getElementById("copy-status");
        button.addEventListener("click", async () => {{
            try {{
                await navigator.clipboard.writeText(report);
                status.textContent = "Copied. You can paste it into ChatGPT now.";
            }} catch (err) {{
                const textarea = document.createElement("textarea");
                textarea.value = report;
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand("copy");
                document.body.removeChild(textarea);
                status.textContent = "Copied using fallback method.";
            }}
        }});
        </script>
        """,
        height=95,
    )


def major_items_to_table(items: list[str]):
    rows = []
    for item in items:
        participant, separator, change = item.rpartition(": ")
        rows.append(
            {
                "Participant": participant if separator else item,
                "Change": change if separator else "",
            }
        )
    return rows


st.title("Webb-site CCASS Extractor")
st.caption("Public CCASS data extraction for research only. This is not investment advice. Please avoid high-frequency fetching.")

with st.sidebar:
    st.header("Input")
    input_type = st.radio("Input Type", ["Stock Code", "Webb-site Issue ID"], index=0)

    if input_type == "Stock Code":
        user_input = st.text_input(
            "Stock Code",
            placeholder="e.g. 03321, 06080, 01417, 01953",
            help="Enter a HK stock code with leading zero if applicable, e.g. 03321.",
        )
    else:
        user_input = st.text_input(
            "Webb-site Issue ID",
            placeholder="e.g. 25298, 27882, 25486, 29176",
            help="Use this if you already know the Webb-site internal issue ID.",
        )

    st.caption("Stock Code examples: 03321, 06080, 01417, 01953")
    st.caption("Issue ID examples: 27882, 25298, 25486, 29176")

    timeout = st.number_input(
        "Timeout per page seconds",
        min_value=10,
        max_value=120,
        value=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "60")),
        step=5,
    )
    headless = st.toggle("Playwright headless", value=env_bool("PLAYWRIGHT_HEADLESS", True))
    fetch_clicked = st.button("Fetch Webb-site Data", type="primary", use_container_width=True)

if "results" not in st.session_state:
    st.session_state.results = None
    st.session_state.lookup = empty_lookup()
    st.session_state.manual_issue_id = ""

if fetch_clicked:
    raw_input = user_input.strip()
    st.session_state.results = None
    st.session_state.lookup = empty_lookup()

    if not raw_input:
        st.error("Please enter a Stock Code or Webb-site Issue ID.")
    else:
        lookup = empty_lookup()
        issue_id = ""
        stock_code = ""

        with st.status("Resolving Webb-site issue ID...", expanded=True) as status:
            if input_type == "Stock Code":
                stock_code = clean_stock_code(raw_input)
                st.write(f"Stock code: {stock_code}")
                st.write("Opening orgdata page and extracting the real Webb-site issue ID from links.")
                lookup = resolve_issue_id_from_stock(stock_code, timeout=int(timeout), headless=headless)
                issue_id = lookup.issue_id
                if lookup.status == "success":
                    st.write(f"Webb-site issue ID: {issue_id}")
                    st.write(f"ID lookup method: {lookup.method}")
                    if lookup.message:
                        st.write(lookup.message)
                else:
                    st.error(lookup.message)
            else:
                issue_id = raw_input
                lookup = IssueLookup(
                    stock_code="",
                    issue_id=issue_id,
                    method="manually entered",
                    status="success",
                    message="Issue ID was manually entered.",
                )
                st.write(f"Webb-site issue ID: {issue_id}")
                st.write("ID lookup method: manually entered")
            status.update(label="Issue ID resolution complete", state="complete")

        if issue_id:
            with st.status("Fetching Company / Holdings / Changes / Big Changes / Concentration...", expanded=True) as status:
                results = fetch_all(issue_id, stock_code=stock_code, timeout=int(timeout), headless=headless)
                for section in SECTIONS:
                    result = results.get(section)
                    if result:
                        st.write(f"{section}: {'success' if result.ok else 'failed'}, tables={len(result.tables)}")
                status.update(label="Fetch complete", state="complete")
            st.session_state.results = results
            st.session_state.lookup = lookup

results = st.session_state.results
lookup = st.session_state.lookup

st.subheader("Resolved Metadata")
meta_cols = st.columns(4)
meta_cols[0].metric("Stock code", lookup.stock_code or "-")
meta_cols[2].metric("Webb-site issue ID", lookup.issue_id or "-")
meta_cols[3].metric("ID lookup status", lookup.status or "-")
if lookup.method:
    st.caption(f"ID lookup method: {lookup.method}")
if lookup.message:
    st.info(lookup.message)

if not results:
    st.info("Choose an input type, enter a value, then click Fetch Webb-site Data.")
    st.stop()

manual_overrides = {}
with st.expander("Advanced table selection", expanded=False):
    st.caption("Leave these on Auto select unless a parsed table is missing. Manual choices apply after the page reruns.")
    columns = st.columns(5)
    for idx, section in enumerate(SECTIONS):
        result = results.get(section)
        option = columns[idx].selectbox(section, table_options(result), key=f"selector_v3_{section}")
        selected = option_to_index(option)
        if selected is not None:
            manual_overrides[section] = selected

parsed = parse_results(
    lookup.issue_id,
    results,
    stock_code=lookup.stock_code,
    id_lookup_method=lookup.method,
    id_lookup_status=lookup.status,
    selected_indices=manual_overrides,
)
report = build_report(parsed, results)
fetch_summary = build_fetch_summary(parsed, results)
json_ready = parsed_to_json_ready(parsed, results)

with meta_cols[1]:
    st.caption("Stock name")
    st.markdown(f"**{parsed.stock_name or '-'}**")

st.subheader("Download This Stock")
st.caption("One CSV contains Holdings, Changes, Big Changes and Concentration with source URL, fetched time and data meaning.")
top_dl1, top_dl2 = st.columns([2, 1])
top_csv = combined_stock_csv(parsed, results)
with top_dl1:
    st.download_button(
        "Download All CCASS Data CSV",
        top_csv,
        f"{get_download_base(parsed)}_all_ccass_data.csv",
        "text/csv",
        key=f"top_{get_download_base(parsed)}_all_ccass_data_csv",
        use_container_width=True,
    )
with top_dl2:
    st.download_button(
        "Download Excel",
        excel_bytes(parsed, results),
        f"{get_download_base(parsed)}_all_sections.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"top_{get_download_base(parsed)}_all_sections_xlsx",
        use_container_width=True,
    )
with st.expander("CSV content preview", expanded=False):
    csv_text = top_csv.decode("utf-8-sig", errors="replace")
    preview_lines = "\n".join(csv_text.splitlines()[:80])
    st.text_area("First 80 CSV lines", preview_lines, height=260, key=f"top_{get_download_base(parsed)}_csv_preview")

st.markdown(
    """
    **Jump to:** [Fetch Summary](#fetch-summary) | [All Tables](#all-tables) |
    [Company](#company) | [Holdings](#holdings) | [Changes](#changes) |
    [Big Changes](#big-changes) | [Concentration](#concentration) |
    [Raw Previews](#raw-table-previews) | [Copy for ChatGPT](#copy-for-chatgpt) |
    [Downloads](#download-files)
    """
)

st.markdown('<div id="fetch-summary"></div>', unsafe_allow_html=True)
st.subheader("Fetch Summary")
st.dataframe(compact_fetch_summary(fetch_summary), use_container_width=True)
with st.expander("Source URLs", expanded=False):
    st.dataframe(fetch_summary[["Section", "URL"]], use_container_width=True)
if parsed.analysis_warnings:
    for warning in parsed.analysis_warnings:
        st.warning(warning)

st.divider()
st.markdown('<div id="all-tables"></div>', unsafe_allow_html=True)
render_all_parsed_tables(parsed)

st.divider()
st.markdown('<div id="company"></div>', unsafe_allow_html=True)
section = "Company / orgdata"
parse = parsed.section_parses.get(section)
render_section(section, results.get(section), parse.selected_table_index if parse else "", parsed.company_table)

st.divider()
st.markdown('<div id="holdings"></div>', unsafe_allow_html=True)
section = "Holdings"
parse = parsed.section_parses.get(section)
summary = (
    f"Data date: {parsed.holdings_data_date or 'not available'} | "
    f"Largest participant: {parsed.largest_participant or 'not available'} | "
    f"Top 5: {parsed.top5_cumulative_pct or 'not available'} | Top 10: {parsed.top10_cumulative_pct or 'not available'}"
)
render_section(section, results.get(section), parse.selected_table_index if parse else "", parsed.holdings_table, summary)

st.divider()
st.markdown('<div id="changes"></div>', unsafe_allow_html=True)
section = "Changes"
parse = parsed.section_parses.get(section)
summary = (
    f"Date range: {parsed.changes_date_range or 'not available'} | "
    f"Trading date: {parsed.changes_trading_date or 'not available'} | "
    f"Volume: {parsed.volume or 'not available'} | Turnover: {parsed.turnover or 'not available'}"
)
render_section(section, results.get(section), parse.selected_table_index if parse else "", parsed.changes_table, summary)
if parsed.major_increases:
    st.markdown("**Major increases**")
    st.dataframe(major_items_to_table(parsed.major_increases), use_container_width=True)
if parsed.major_decreases:
    st.markdown("**Major decreases**")
    st.dataframe(major_items_to_table(parsed.major_decreases), use_container_width=True)
if parsed.changes_flags:
    st.markdown("**Changes auto flags**")
    for flag in parsed.changes_flags:
        st.info(flag)

st.divider()
st.markdown('<div id="big-changes"></div>', unsafe_allow_html=True)
section = "Big Changes"
parse = parsed.section_parses.get(section)
render_section(
    section,
    results.get(section),
    parse.selected_table_index if parse else "",
    parsed.big_changes_table,
    f"Latest date: {parsed.big_changes_latest_date or 'not available'}",
)
for flag in parsed.transfer_flags:
    st.warning(flag)

st.divider()
st.markdown('<div id="concentration"></div>', unsafe_allow_html=True)
section = "Concentration"
parse = parsed.section_parses.get(section)
render_section(
    section,
    results.get(section),
    parse.selected_table_index if parse else "",
    parsed.concentration_table,
    f"Latest date: {parsed.concentration_latest_date or 'not available'}",
)
if parse and parse.status == "partial success":
    st.warning(parse.error)
render_concentration_change(parsed)

st.divider()
st.markdown('<div id="raw-table-previews"></div>', unsafe_allow_html=True)
st.subheader("Raw Table Previews")
for record in table_preview_records(results):
    with st.expander(f"{record['section']} | table {record['table_index']} | {record['shape']}", expanded=False):
        st.caption("Columns: " + ", ".join(record["columns"]))
        st.json(record["preview"])

st.divider()
st.markdown('<div id="copy-for-chatgpt"></div>', unsafe_allow_html=True)
st.markdown('<div id="copy-for-chat-gpt"></div>', unsafe_allow_html=True)
st.subheader("Copy for ChatGPT")
render_copy_report(report)

st.divider()
st.markdown('<div id="download-files"></div>', unsafe_allow_html=True)
st.subheader("Download Files")
render_download_buttons(parsed, results, report, "bottom")
