from __future__ import annotations

import json
import os
import importlib

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

try:
    import altair as alt
except ImportError:
    alt = None

import utils.exporters as exporters
from utils.hkexnews import HKEXAnnouncementResult, fetch_announcements
from utils.fetcher import (
    USER_AGENT,
    IssueLookup,
    clean_stock_code,
    extract_tables_from_html,
    fetch_all,
    fetch_with_requests,
    issue_urls,
    resolve_issue_id_from_stock,
)
from utils.parser import SECTIONS, build_fetch_summary, parse_results, table_preview_records
from utils.report import build_report
from utils.events import events_url, parse_events_html, parse_events_name
from utils.f10_managers import f10_managers_url, parse_f10_managers_html
from utils.f10_equity import f10_equity_url, parse_f10_buybacks, parse_f10_share_changes

exporters = importlib.reload(exporters)
combined_stock_csv = exporters.combined_stock_csv
excel_bytes = exporters.excel_bytes
parsed_to_json_ready = exporters.parsed_to_json_ready


def csv_bytes(df):
    if hasattr(exporters, "csv_bytes"):
        return exporters.csv_bytes(df)
    return df.to_csv(index=False).encode("utf-8-sig")


st.set_page_config(page_title="Webb-site CCASS Extractor", layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.4rem; max-width: 1400px; }

    /* Hero */
    .ccass-hero {
        border-radius: 16px;
        padding: 22px 28px;
        margin-bottom: 14px;
        background:
            radial-gradient(1100px 320px at 88% -20%, rgba(45, 212, 191, 0.28), transparent 60%),
            linear-gradient(135deg, #0b2545 0%, #13315c 55%, #134e4a 100%);
        color: #f8fafc;
        box-shadow: 0 14px 34px rgba(11, 37, 69, 0.30);
    }
    .ccass-hero h1 { margin: 0; font-size: 1.6rem; font-weight: 700; letter-spacing: .2px; }
    .ccass-hero p { margin: 6px 0 0 0; font-size: .86rem; color: #cbd5e1; }

    /* KPI metric cards */
    [data-testid="stMetric"] {
        background: var(--secondary-background-color, #f8fafc);
        border: 1px solid rgba(148, 163, 184, 0.28);
        border-radius: 12px;
        padding: 12px 14px;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
    }
    [data-testid="stMetricLabel"] { opacity: .75; font-size: .74rem; }

    /* Tabs -> pill / panel feel */
    .stTabs [data-baseweb="tab-list"] { gap: 6px; flex-wrap: wrap; }
    .stTabs [data-baseweb="tab"] {
        border-radius: 10px 10px 0 0;
        padding: 8px 16px;
        font-weight: 600;
        font-size: .92rem;
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #0b2545, #134e4a);
        color: #fff !important;
    }

    /* Dataframes + dividers */
    [data-testid="stDataFrame"] { border-radius: 10px; overflow: hidden; }
    hr { margin: 0.8rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)


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
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.download_button("Holdings CSV", csv_bytes(parsed.holdings_table), f"{base}_holdings.csv", "text/csv", key=f"{key_prefix}_{base}_holdings_csv")
    col2.download_button("Changes CSV", csv_bytes(parsed.changes_table), f"{base}_changes.csv", "text/csv", key=f"{key_prefix}_{base}_changes_csv")
    col3.download_button("Big Changes CSV", csv_bytes(parsed.big_changes_table), f"{base}_bigchanges.csv", "text/csv", key=f"{key_prefix}_{base}_bigchanges_csv")
    col4.download_button("Concentration CSV", csv_bytes(parsed.concentration_table), f"{base}_concentration.csv", "text/csv", key=f"{key_prefix}_{base}_concentration_csv")
    col5.download_button("Price CSV", csv_bytes(parsed.price_history_table), f"{base}_price_history.csv", "text/csv", key=f"{key_prefix}_{base}_price_history_csv")

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
        ("Price History", parsed.price_history_latest_date, parsed.price_history_table),
    ]
    for section, date_value, df in sections:
        st.markdown(f"### {section}")
        st.caption(f"Data date / latest date: {date_value or 'not available'}")
        if df is not None and not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.warning(f"{section} parsed table is not available. Check Raw Table Previews.")



def empty_announcements(stock_code: str = "", period_years: int = 1) -> HKEXAnnouncementResult:
    return HKEXAnnouncementResult(stock_code=stock_code, period_years=period_years, table=pd.DataFrame())


def render_hkex_announcements(announcements: HKEXAnnouncementResult) -> None:
    st.subheader("HKEX Announcements")
    if not announcements or announcements.table is None:
        st.info("HKEX announcements were not fetched.")
        return
    period = f"{announcements.period_years} year" if announcements.period_years == 1 else f"{announcements.period_years} years"
    st.caption(
        f"Stock {announcements.stock_code or '-'} {announcements.stock_name or ''} | "
        f"Period: {announcements.from_date or '-'} to {announcements.to_date or '-'} ({period}) | "
        f"HKEX total count: {announcements.total_count}"
    )
    if announcements.url:
        st.caption(announcements.url)
    if announcements.error:
        st.warning(announcements.error)
    if announcements.table.empty:
        st.info("No HKEX announcements found for this period.")
        return
    display_cols = ["Publish time", "Category", "Title", "File info", "URL"]
    st.dataframe(announcements.table[display_cols], use_container_width=True, hide_index=True)
def render_concentration_change(parsed) -> None:
    st.markdown("**Recent 5 trading days concentration change**")
    if not parsed.concentration_5day_change:
        st.caption("Not enough concentration history to calculate.")
        return
    st.dataframe(
        [{"Metric": key, "Change": value} for key, value in parsed.concentration_5day_change.items()],
        use_container_width=True,
    )


def numeric_percent(value) -> float | None:
    text = str(value or "").replace(",", "").replace("%", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def numeric_amount(value) -> float | None:
    text = str(value or "").upper().replace(",", "").replace("HK$", "").replace("$", "").strip()
    if not text:
        return None
    multiplier = 1.0
    if text.endswith("K"):
        multiplier = 1_000.0
        text = text[:-1]
    elif text.endswith("M"):
        multiplier = 1_000_000.0
        text = text[:-1]
    elif text.endswith("B"):
        multiplier = 1_000_000_000.0
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def format_hk_amount(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    if abs(value) >= 1_000_000_000:
        return f"HK${value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"HK${value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"HK${value / 1_000:.1f}K"
    return f"HK${value:,.0f}"


def format_share_amount(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B shares"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M shares"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.0f}K shares"
    return f"{value:,.0f} shares"


def format_price(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"HK${value:.4f}".rstrip("0").rstrip(".")


def format_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def build_price_history_chart_data(price_history_table: pd.DataFrame, window: str = "1Y") -> pd.DataFrame:
    if price_history_table is None or price_history_table.empty:
        return pd.DataFrame()
    rows = []
    for _, row in price_history_table.iterrows():
        date = pd.to_datetime(row.get("Date"), errors="coerce")
        close = numeric_amount(row.get("Close"))
        turnover = numeric_amount(row.get("Turnover"))
        volume = numeric_amount(row.get("Volume"))
        vwap = numeric_amount(row.get("VWAP"))
        if pd.isna(date) or close is None:
            continue
        rows.append(
            {
                "Date": date,
                "Close": close,
                "Turnover": turnover,
                "Volume": volume,
                "VWAP": vwap,
            }
        )
    data = pd.DataFrame(rows)
    if data.empty:
        return data
    latest = data["Date"].max()
    window_days = {"1M": 31, "3M": 93, "6M": 186, "1Y": 365}.get(window)
    if window_days:
        data = data[data["Date"] >= latest - pd.Timedelta(days=window_days)]
    data = data.sort_values("Date").reset_index(drop=True)
    data["DailyChangePct"] = data["Close"].pct_change() * 100
    data["CloseVsVWAPPct"] = ((data["Close"] - data["VWAP"]) / data["VWAP"]) * 100
    data["GapDays"] = data["Date"].diff().dt.days.fillna(1)
    data["Segment"] = (data["GapDays"] > 10).cumsum()
    data["CloseLabel"] = data["Close"].map(format_price)
    data["VWAPLabel"] = data["VWAP"].map(format_price)
    data["TurnoverLabel"] = data["Turnover"].map(format_hk_amount)
    data["VolumeLabel"] = data["Volume"].map(format_share_amount)
    data["DailyChangeLabel"] = data["DailyChangePct"].map(format_pct)
    data["CloseVsVWAPLabel"] = data["CloseVsVWAPPct"].map(format_pct)
    return data


def suspended_gap_data(chart_data: pd.DataFrame) -> pd.DataFrame:
    if chart_data is None or chart_data.empty:
        return pd.DataFrame(columns=["Start", "End", "Label"])
    rows = []
    ordered = chart_data.sort_values("Date")
    previous_date = None
    for _, row in ordered.iterrows():
        current_date = row["Date"]
        if previous_date is not None and (current_date - previous_date).days > 10:
            rows.append({"Start": previous_date, "End": current_date, "Label": "Trading suspended / no price records"})
        previous_date = current_date
    return pd.DataFrame(rows)


def price_history_stats(chart_data: pd.DataFrame, issued_securities: str = "") -> dict[str, str]:
    if chart_data is None or chart_data.empty:
        return {}
    latest = chart_data.sort_values("Date").iloc[-1]
    previous = chart_data.sort_values("Date").iloc[-2] if len(chart_data) >= 2 else None
    daily_change = None
    if previous is not None and previous.get("Close"):
        daily_change = (latest["Close"] - previous["Close"]) / previous["Close"] * 100
    turnover_avg = chart_data["Turnover"].dropna().tail(20).mean()
    turnover_vs_avg = latest["Turnover"] / turnover_avg * 100 if turnover_avg and not pd.isna(turnover_avg) else None
    issued = numeric_amount(issued_securities)
    turnover_rate = latest["Volume"] / issued * 100 if issued and latest.get("Volume") else None
    close_vs_vwap = latest.get("CloseVsVWAPPct")
    latest_date = pd.to_datetime(latest["Date"]).strftime("%d %b %Y")
    return {
        "date": latest_date,
        "close": format_price(latest.get("Close")),
        "daily_change": format_pct(daily_change),
        "daily_vwap": format_price(latest.get("VWAP")),
        "close_vs_vwap": format_pct(close_vs_vwap),
        "turnover": format_hk_amount(latest.get("Turnover")),
        "turnover_vs_avg": f"{turnover_vs_avg:.0f}% of 20-day average" if turnover_vs_avg is not None else "-",
        "volume": format_share_amount(latest.get("Volume")),
        "turnover_rate": f"Estimated turnover rate {turnover_rate:.2f}%" if turnover_rate is not None else "Estimated turnover rate n/a",
    }


def parse_cost_lines(text: str) -> list[dict[str, object]]:
    rows = []
    for line in (text or "").splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue
        price = numeric_amount(parts[1])
        if price is not None:
            rows.append({"Label": parts[0] or "Cost line", "Price": price})
    return rows


def parse_event_markers(text: str) -> list[dict[str, object]]:
    rows = []
    for line in (text or "").splitlines():
        parts = [part.strip() for part in line.split(",", 1)]
        if len(parts) < 2:
            continue
        date = pd.to_datetime(parts[0], errors="coerce")
        if not pd.isna(date):
            rows.append({"Date": date, "Label": parts[1]})
    return rows


def build_ccass_concentration_line_data(concentration_table: pd.DataFrame, window: str = "1Y") -> pd.DataFrame:
    if concentration_table is None or concentration_table.empty:
        return pd.DataFrame()
    rows = []
    for _, row in concentration_table.iterrows():
        date = pd.to_datetime(row.get("Date"), errors="coerce")
        top5 = numeric_percent(row.get("Top 5 %"))
        top10 = numeric_percent(row.get("Top 10 %"))
        top10_ncip = numeric_percent(row.get("Top 10 + NCIP %"))
        if pd.isna(date):
            continue
        ncip = None
        if top10 is not None and top10_ncip is not None:
            ncip = max(top10_ncip - top10, 0)
        top5_ncip = top5 + ncip if top5 is not None and ncip is not None else None
        series = [
            ("Top5 + 非流通股票", top5_ncip),
            ("Top10 + 非流通股票", top10_ncip),
        ]
        for label, value in series:
            # Drop out-of-range artifacts (e.g. a stale issued-share base can
            # yield >100% concentration). A single such point would blow up the
            # auto-scaled y-axis and flatten the real 0-100% band into a line at
            # the bottom. These bad points are already flagged in the warnings.
            if value is not None and 0 <= value <= 100.5:
                rows.append({"Date": date, "Metric": label, "Percent": value, "PercentLabel": f"{value:.2f}%"})
    data = pd.DataFrame(rows)
    if data.empty:
        return data
    latest = data["Date"].max()
    window_days = {"1M": 31, "3M": 93, "6M": 186, "1Y": 365}.get(window)
    if window_days:
        data = data[data["Date"] >= latest - pd.Timedelta(days=window_days)]
    return data.sort_values("Date")


def latest_ccass_concentration_values(concentration_data: pd.DataFrame) -> pd.DataFrame:
    if concentration_data is None or concentration_data.empty:
        return pd.DataFrame()
    return (
        concentration_data.sort_values("Date")
        .groupby("Metric", as_index=False)
        .tail(1)
        .sort_values("Metric")
        [["Metric", "Percent", "PercentLabel", "Date"]]
    )


def ccass_concentration_chart(concentration_data: pd.DataFrame, height: int = 145):
    if concentration_data is None or concentration_data.empty or alt is None:
        return None
    return (
        alt.Chart(concentration_data)
        .mark_line(strokeWidth=2.2)
        .encode(
            x=alt.X("Date:T", title="Date"),
            y=alt.Y("Percent:Q", title="CCASS 股權集中度 (%)", scale=alt.Scale(zero=False)),
            color=alt.Color(
                "Metric:N",
                scale=alt.Scale(
                    domain=["Top5 + 非流通股票", "Top10 + 非流通股票"],
                    range=["#f97316", "#2563eb"],
                ),
                legend=alt.Legend(orient="top", title=None),
            ),
            tooltip=[
                alt.Tooltip("Date:T", title="Date", format="%Y-%m-%d"),
                alt.Tooltip("Metric:N", title="Metric"),
                alt.Tooltip("Percent:Q", title="Percent", format=".2f"),
            ],
        )
        .properties(height=height, title="CCASS 股權集中度")
    )


def price_turnover_chart(
    chart_data: pd.DataFrame,
    bar_mode: str = "Turnover",
    height: int = 320,
    cost_lines: list[dict[str, object]] | None = None,
    event_markers: list[dict[str, object]] | None = None,
):
    if chart_data is None or chart_data.empty or alt is None:
        return None
    data = chart_data.copy()
    if bar_mode == "Volume":
        data["BarValue"] = data["Volume"] / 1_000_000
        bar_title = "Volume (million shares)"
    else:
        data["BarValue"] = data["Turnover"] / 1_000_000
        bar_title = "Turnover (HK$ million)"

    line_rows = []
    for _, row in data.iterrows():
        line_rows.append({"Date": row["Date"], "Series": "Close Price", "Value": row["Close"], "Segment": row["Segment"]})
        if not pd.isna(row.get("VWAP")):
            line_rows.append({"Date": row["Date"], "Series": "Daily VWAP", "Value": row["VWAP"], "Segment": row["Segment"]})
    if cost_lines:
        min_date = data["Date"].min()
        max_date = data["Date"].max()
        for idx, item in enumerate(cost_lines, start=1):
            label = str(item["Label"])
            line_rows.append({"Date": min_date, "Series": label, "Value": item["Price"], "Segment": f"cost-{idx}"})
            line_rows.append({"Date": max_date, "Series": label, "Value": item["Price"], "Segment": f"cost-{idx}"})
    line_data = pd.DataFrame(line_rows)
    gap_data = suspended_gap_data(data)
    event_data = pd.DataFrame(event_markers or [])
    series_domain = ["Close Price", "Daily VWAP"] + [str(item["Label"]) for item in (cost_lines or [])]
    series_range = ["#2563eb", "#dc2626", "#7c3aed", "#059669", "#d97706", "#be123c", "#0891b2"][: len(series_domain)]
    dash_range = [[1, 0], [6, 4]] + [[3, 3] for _ in (cost_lines or [])]

    tooltip = [
        alt.Tooltip("Date:T", title="Date", format="%d %b %Y"),
        alt.Tooltip("CloseLabel:N", title="Close"),
        alt.Tooltip("VWAPLabel:N", title="Daily VWAP"),
        alt.Tooltip("CloseVsVWAPLabel:N", title="Close vs VWAP"),
        alt.Tooltip("TurnoverLabel:N", title="Turnover"),
        alt.Tooltip("VolumeLabel:N", title="Volume"),
        alt.Tooltip("DailyChangeLabel:N", title="Daily change"),
    ]
    layers = []
    if not gap_data.empty:
        layers.append(
            alt.Chart(gap_data)
            .mark_rect(color="#e5e7eb", opacity=0.55)
            .encode(
                x=alt.X("Start:T"),
                x2="End:T",
                tooltip=[alt.Tooltip("Label:N", title="Status"), alt.Tooltip("Start:T", title="From", format="%d %b %Y"), alt.Tooltip("End:T", title="To", format="%d %b %Y")],
            )
        )

    base = alt.Chart(data).encode(x=alt.X("Date:T", title="Date"))
    layers.append(
        base.mark_bar(opacity=0.28, color="#94a3b8").encode(
            y=alt.Y("BarValue:Q", title=bar_title, axis=alt.Axis(format="~s")),
            tooltip=tooltip,
        )
    )
    layers.append(
        alt.Chart(line_data)
        .mark_line(strokeWidth=2)
        .encode(
            x=alt.X("Date:T", title="Date"),
            y=alt.Y("Value:Q", title="Price (HK$)", scale=alt.Scale(zero=False)),
            color=alt.Color(
                "Series:N",
                scale=alt.Scale(domain=series_domain, range=series_range),
                legend=alt.Legend(orient="top", title=None),
            ),
            strokeDash=alt.StrokeDash(
                "Series:N",
                scale=alt.Scale(domain=series_domain, range=dash_range),
                legend=None,
            ),
            detail="Segment:N",
            tooltip=[
                alt.Tooltip("Series:N", title="Line"),
                alt.Tooltip("Value:Q", title="Price", format=".4f"),
                alt.Tooltip("Date:T", title="Date", format="%d %b %Y"),
            ],
        )
    )
    if not event_data.empty:
        layers.append(
            alt.Chart(event_data)
            .mark_rule(color="#111827", strokeDash=[2, 2], opacity=0.65)
            .encode(
                x=alt.X("Date:T"),
                tooltip=[alt.Tooltip("Date:T", title="Event date", format="%d %b %Y"), alt.Tooltip("Label:N", title="Event")],
            )
        )
    layers.append(base.mark_point(opacity=0, size=90).encode(tooltip=tooltip))
    return alt.layer(*layers).resolve_scale(y="independent").properties(height=height)


def render_ccass_concentration_summary(parsed, window: str = "1Y", chart_height: int = 150) -> None:
    concentration_data = build_ccass_concentration_line_data(parsed.concentration_table, window)
    if concentration_data.empty:
        st.caption("CCASS concentration data is not available for Top5/Top10 + non-circulating shares.")
        return
    latest = latest_ccass_concentration_values(concentration_data)
    metric_cols = st.columns(2)
    for idx, (_, row) in enumerate(latest.iterrows()):
        metric_cols[min(idx, 1)].metric(row["Metric"], f"{row['Percent']:.2f}%", pd.to_datetime(row["Date"]).strftime("%Y-%m-%d"))
    chart = ccass_concentration_chart(concentration_data, height=chart_height)
    if chart is not None:
        st.altair_chart(chart, use_container_width=True)


def render_price_history(parsed) -> None:
    st.markdown("**Price & Turnover History**")
    if alt is None:
        st.warning("Altair is not installed, so the chart cannot be drawn. Install requirements.txt and rerun the app.")
        return
    control1, control2 = st.columns([1, 1])
    window = control1.radio("Range", ["1M", "3M", "6M", "1Y", "Max"], index=3, horizontal=True)
    bar_mode = control2.radio("Bars", ["Turnover", "Volume"], index=0, horizontal=True)
    with st.expander("Cost / event lines", expanded=False):
        st.caption("Optional manual overlays. Use one per line: label,price or YYYY-MM-DD,label.")
        note1, note2 = st.columns(2)
        cost_text = note1.text_area("Cost lines", placeholder="GO Price,0.365\nPlacing Price,0.520\nNew Controller Cost,0.610", height=110)
        event_text = note2.text_area("Event markers", placeholder="2026-05-15,Trading resumed\n2026-06-03,CCASS transfer", height=110)
    chart_data = build_price_history_chart_data(parsed.price_history_table, window or "1Y")
    if chart_data.empty:
        st.caption("Price history is not available. Webb-site hpu.asp may be blocked or the table format may have changed.")
        return

    stats = price_history_stats(chart_data, parsed.issued_securities)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Latest Close", stats.get("close", "-"), stats.get("daily_change", "-"))
    col2.metric("Latest Daily VWAP", stats.get("daily_vwap", "-"), f"Close vs VWAP {stats.get('close_vs_vwap', '-')}")
    col3.metric("Turnover", stats.get("turnover", "-"), stats.get("turnover_vs_avg", "-"))
    col4.metric("Volume", stats.get("volume", "-"), stats.get("turnover_rate", "-"))

    chart = price_turnover_chart(
        chart_data,
        bar_mode or "Turnover",
        height=340,
        cost_lines=parse_cost_lines(cost_text),
        event_markers=parse_event_markers(event_text),
    )
    st.altair_chart(chart, use_container_width=True)
    st.caption(f"Price history comes from Webb-site hpu.asp. Latest daily figures are as of {stats.get('date', '-')}. Grey bands mark long gaps with no price records.")
    # Concentration chart intentionally NOT rendered here: the DT rainbow
    # section below has its own CCASS 股權集中度 chart (matching DT's layout of
    # distribution chart -> concentration), so rendering it here duplicated it.


def build_rainbow_chart_data(concentration_table: pd.DataFrame) -> pd.DataFrame:
    if concentration_table is None or concentration_table.empty:
        return pd.DataFrame()

    rows = []
    band_order = {"Top 5": 1, "Top 6-10": 2, "NCIP": 3, "Other CCASS": 4, "Outside CCASS": 5}
    for _, row in concentration_table.iterrows():
        date = pd.to_datetime(row.get("Date"), errors="coerce")
        if pd.isna(date):
            continue

        top5 = numeric_percent(row.get("Top 5 %"))
        top10 = numeric_percent(row.get("Top 10 %"))
        top10_ncip = numeric_percent(row.get("Top 10 + NCIP %"))
        stake = numeric_percent(row.get("Stake in CCASS %"))
        if top5 is None or top10 is None:
            continue

        top10_ncip = top10_ncip if top10_ncip is not None else top10
        stake = stake if stake is not None else max(top10_ncip, top10)
        bands = [
            ("Top 5", top5),
            ("Top 6-10", top10 - top5),
            ("NCIP", top10_ncip - top10),
            ("Other CCASS", stake - top10_ncip),
            ("Outside CCASS", 100 - stake),
        ]
        for band, pct in bands:
            if pct is not None and pct > 0:
                rows.append({"Date": date, "Band": band, "BandOrder": band_order[band], "Percent": pct})

    return pd.DataFrame(rows)


def render_rainbow_chart(parsed) -> None:
    st.markdown("**Concentration Band Chart (not DT rainbow)**")
    if alt is None:
        st.warning("Altair is not installed, so the chart cannot be drawn. Install requirements.txt and rerun the app.")
        return
    chart_data = build_rainbow_chart_data(parsed.concentration_table)
    if chart_data.empty:
        st.caption("Not enough concentration history to draw the concentration band chart.")
        return

    band_order = ["Top 5", "Top 6-10", "NCIP", "Other CCASS", "Outside CCASS"]
    chart = (
        alt.Chart(chart_data)
        .mark_area(interpolate="monotone")
        .encode(
            x=alt.X("Date:T", title="Date"),
            y=alt.Y("Percent:Q", stack="zero", title="Share of issued securities (%)", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color(
                "Band:N",
                sort=band_order,
                scale=alt.Scale(
                    domain=band_order,
                    range=["#7f1d1d", "#f97316", "#facc15", "#22c55e", "#38bdf8"],
                ),
                title="Holder group",
            ),
            order=alt.Order("BandOrder:Q", sort="ascending"),
            tooltip=[
                alt.Tooltip("Date:T", title="Date", format="%Y-%m-%d"),
                alt.Tooltip("Band:N", title="Group"),
                alt.Tooltip("Percent:Q", title="Band %", format=".2f"),
            ],
        )
        .properties(height=420)
    )
    st.altair_chart(chart, use_container_width=True)
    st.caption("Bands are derived from concentration history: Top 5, Top 6-10, NCIP, other CCASS holdings, and shares outside CCASS.")


def pick_history_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    normalized = {str(col).lower().replace(".", "").strip(): col for col in df.columns}
    for candidate in candidates:
        wanted = candidate.lower().replace(".", "").strip()
        for normalized_name, original in normalized.items():
            if wanted in normalized_name:
                return original
    return None


def participant_label(name: str, ccass_id: str) -> str:
    clean = " ".join(str(name or "").split())
    clean = clean.replace("(HONG KONG)", "(HK)").replace("COMPANY LIMITED", "CO LTD")
    if len(clean) > 34:
        clean = clean[:31].rstrip() + "..."
    return f"{clean} ({ccass_id})" if ccass_id else clean


def current_top_participants(parsed, limit: int) -> list[dict[str, object]]:
    if parsed.holdings_table is None or parsed.holdings_table.empty:
        return []
    rows = []
    for idx, row in parsed.holdings_table.head(limit).iterrows():
        ccass_id = str(row.get("CCASS ID", "") or "").strip()
        if not ccass_id:
            continue
        name = str(row.get("Participant", "") or "").strip()
        rows.append(
            {
                "ccass_id": ccass_id,
                "name": name,
                "label": participant_label(name, ccass_id),
                "order": len(rows) + 1,
            }
        )
    return rows


def sampled_history_dates(concentration_table: pd.DataFrame, max_points: int) -> list[str]:
    if concentration_table is None or concentration_table.empty or "Date" not in concentration_table:
        return []
    dates = pd.to_datetime(concentration_table["Date"], errors="coerce").dropna().drop_duplicates().sort_values()
    if dates.empty:
        return []
    latest = dates.max()
    recent = dates[dates >= latest - pd.Timedelta(days=365)]
    dates = recent if not recent.empty else dates
    if len(dates) > max_points:
        positions = sorted({round(i * (len(dates) - 1) / (max_points - 1)) for i in range(max_points)})
        dates = dates.iloc[positions]
    return [date.strftime("%Y-%m-%d") for date in dates]


def parse_historical_holdings_table(table: pd.DataFrame, participants: list[dict[str, object]], date: str) -> list[dict[str, object]]:
    if table is None or table.empty:
        return []
    df = table.copy()
    id_col = pick_history_column(df, ["CCASS ID", "Participant ID", "ID"])
    name_col = pick_history_column(df, ["Name"])
    holding_col = pick_history_column(df, ["Holding"])
    stake_col = pick_history_column(df, ["Stake %", "Stake", "%"])
    if not id_col or not stake_col:
        return []

    wanted = {item["ccass_id"]: item for item in participants}
    found = {}
    for _, row in df.iterrows():
        ccass_id = str(row.get(id_col, "") or "").strip()
        if ccass_id not in wanted:
            continue
        found[ccass_id] = {
            "Date": date,
            "Participant": wanted[ccass_id]["label"],
            "ParticipantOrder": wanted[ccass_id]["order"],
            "CCASS ID": ccass_id,
            "Name": row.get(name_col, wanted[ccass_id]["name"]) if name_col else wanted[ccass_id]["name"],
            "Holding": row.get(holding_col, "") if holding_col else "",
            "Stake": numeric_percent(row.get(stake_col)),
        }

    rows = []
    for item in participants:
        record = found.get(item["ccass_id"])
        if record:
            rows.append(record)
        else:
            rows.append(
                {
                    "Date": date,
                    "Participant": item["label"],
                    "ParticipantOrder": item["order"],
                    "CCASS ID": item["ccass_id"],
                    "Name": item["name"],
                    "Holding": "",
                    "Stake": 0.0,
                }
            )
    return rows


def count_holdings_participants(table: pd.DataFrame) -> int:
    """Count CCASS participants in one date's holdings table (DT's 券商數目).

    Participant rows carry an ID like B01955 / C00019 / A00001; aggregate rows
    (Total in CCASS, Issued securities, ...) have no ID and are excluded.
    """
    if table is None or table.empty:
        return 0
    id_col = pick_history_column(table, ["CCASS ID", "Participant ID", "ID"])
    if not id_col:
        return 0
    ids = table[id_col].astype(str).str.strip()
    return int(ids.str.match(r"^[A-Ca-c]\d{5}$").sum())


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_participant_history(issue_id: str, dates: tuple[str, ...], participants: tuple[tuple[str, str, str, int], ...], timeout: int, headless: bool) -> pd.DataFrame:
    import time as _time

    participant_rows = [
        {"ccass_id": ccass_id, "name": name, "label": label, "order": order}
        for ccass_id, name, label, order in participants
    ]

    def best_rows_from_tables(tables, date: str) -> list[dict[str, object]]:
        best_rows: list[dict[str, object]] = []
        best_count = 0
        for table in tables:
            parsed_rows = parse_historical_holdings_table(table, participant_rows, date)
            if len(parsed_rows) > len(best_rows):
                best_rows = parsed_rows
                best_count = count_holdings_participants(table)
        for row in best_rows:
            row["BrokerCount"] = best_count
        return best_rows

    rows = []
    base_url = issue_urls(issue_id)["Holdings"]
    failed_jobs = []
    for date in dates:
        url = base_url.replace(f"?i={issue_id}", f"?d={date}&i={issue_id}")
        result = fetch_with_requests(f"Holdings {date}", url, timeout=timeout)
        if result.ok:
            rows.extend(best_rows_from_tables(result.tables, date))
        else:
            failed_jobs.append((date, url))

    # Second chance over plain HTTP with a pause and longer timeout - covers
    # transient failures / rate limiting without needing a browser.
    browser_jobs = []
    for date, url in failed_jobs:
        _time.sleep(0.5)
        result = fetch_with_requests(f"Holdings {date} (retry)", url, timeout=max(timeout, 30))
        if result.ok:
            rows.extend(best_rows_from_tables(result.tables, date))
        else:
            browser_jobs.append((date, url))

    if browser_jobs:
        # Playwright is a best-effort fallback: on hosts without installed
        # browsers (e.g. Streamlit Cloud) launching raises - skip those dates
        # and render the chart from whatever was fetched instead of crashing.
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(headless=headless)
                except Exception as launch_exc:
                    message = str(launch_exc).lower()
                    missing_headless_shell = "chromium_headless_shell" in message or "chrome-headless-shell" in message
                    if not headless or not missing_headless_shell:
                        raise
                    browser = p.chromium.launch(headless=False)
                context = browser.new_context(
                    user_agent=USER_AGENT,
                    viewport={"width": 1440, "height": 1000},
                    locale="en-US",
                )
                page = context.new_page()
                for date, url in browser_jobs:
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                        page.wait_for_timeout(500)
                        tables = extract_tables_from_html(page.content())
                    except Exception:
                        continue
                    rows.extend(best_rows_from_tables(tables, date))
                browser.close()
        except Exception:
            pass
    return pd.DataFrame(rows)


def render_dt_participant_rainbow(parsed, timeout: int, headless: bool) -> None:
    stock_label = parsed.stock_code or parsed.issue_id or "current stock"
    st.markdown(f"**CCASS 中央結算持股分佈圖 - {stock_label}**")
    st.caption("DT-style rainbow: each colour is one CCASS participant/broker for this stock. This is different from Top 5 / Top 10 concentration.")

    if alt is None:
        st.warning("Altair is not installed, so the chart cannot be drawn. Install requirements.txt and rerun the app.")
        return
    if parsed.holdings_table is None or parsed.holdings_table.empty:
        st.caption("Holdings table is required before participant history can be drawn.")
        return
    if parsed.concentration_table is None or parsed.concentration_table.empty:
        st.caption("Concentration history dates are required before participant history can be sampled.")
        return

    col1, col2, col3, col4 = st.columns([1, 1, 2, 1])
    top_n = col1.number_input("顯示前 N 名參與者", min_value=3, max_value=40, value=12, step=1)
    date_points = col2.number_input("歷史日期數量", min_value=6, max_value=80, value=26, step=2)
    build_clicked = col3.button("生成真正 DT 彩虹圖", type="primary", use_container_width=True)
    align_price = col4.checkbox("合併 Price", value=True)

    participants = current_top_participants(parsed, int(top_n))
    dates = sampled_history_dates(parsed.concentration_table, int(date_points))
    if not participants or not dates:
        st.caption("Not enough parsed holdings/concentration data to prepare the chart.")
        return

    participant_ids = ",".join(str(item["ccass_id"]) for item in participants)
    cache_key = f"{parsed.issue_id}:{participant_ids}:{top_n}:{date_points}:{dates[0]}:{dates[-1]}"
    if st.session_state.get("dt_rainbow_key") != cache_key and not build_clicked:
        st.session_state.pop("dt_rainbow_data", None)

    if build_clicked or st.session_state.get("dt_rainbow_key") == cache_key:
        with st.spinner(f"Fetching {len(dates)} historical holdings pages for {len(participants)} participants..."):
            participant_tuple = tuple(
                (str(item["ccass_id"]), str(item["name"]), str(item["label"]), int(item["order"]))
                for item in participants
            )
            chart_data = fetch_participant_history(parsed.issue_id, tuple(dates), participant_tuple, int(timeout), bool(headless))
        st.session_state.dt_rainbow_key = cache_key
        st.session_state.dt_rainbow_data = chart_data
    else:
        top_preview = ", ".join(str(item["label"]) for item in participants[:3])
        st.info(
            f"按上面的紅色按鈕生成真正 DT 彩虹圖。將抓取 {len(dates)} 個歷史日期、"
            f"{len(participants)} 個此股票的主要 CCASS 參與者。Top examples: {top_preview}"
        )
        return

    chart_data = st.session_state.get("dt_rainbow_data", pd.DataFrame())
    if chart_data.empty:
        st.warning("Historical participant holdings could not be fetched. The source may be blocking requests or the selected dates may be unavailable.")
        return

    palette = [
        "#f97316",
        "#84cc16",
        "#dc2626",
        "#2563eb",
        "#22c55e",
        "#facc15",
        "#db2777",
        "#4f46e5",
        "#06b6d4",
        "#a855f7",
        "#14b8a6",
        "#eab308",
        "#ef4444",
        "#0ea5e9",
        "#65a30d",
        "#9333ea",
        "#f59e0b",
        "#10b981",
        "#64748b",
        "#be123c",
        "#7c2d12",
        "#15803d",
        "#1d4ed8",
        "#c026d3",
        "#ca8a04",
        "#0f766e",
        "#b91c1c",
        "#4338ca",
        "#ea580c",
        "#16a34a",
        "#0284c7",
        "#c2185b",
        "#a16207",
        "#047857",
        "#6d28d9",
        "#991b1b",
        "#0369a1",
        "#4d7c0f",
        "#9d174d",
        "#115e59",
        "#92400e",
        "#1e40af",
        "#86198f",
        "#166534",
    ]
    domain = [item["label"] for item in participants]
    rainbow_chart = (
        alt.Chart(chart_data)
        .mark_area(interpolate="monotone")
        .encode(
            x=alt.X("Date:T", title="Date"),
            y=alt.Y("Stake:Q", stack="zero", title="持股百分比 (%)"),
            color=alt.Color(
                "Participant:N",
                sort=domain,
                scale=alt.Scale(domain=domain, range=palette[: len(domain)]),
                title="CCASS 參與者",
                legend=alt.Legend(orient="bottom", columns=2, labelLimit=360),
            ),
            order=alt.Order("ParticipantOrder:Q", sort="ascending"),
            tooltip=[
                alt.Tooltip("Date:T", title="Date", format="%Y-%m-%d"),
                alt.Tooltip("Participant:N", title="CCASS 參與者"),
                alt.Tooltip("Stake:Q", title="持股 %", format=".2f"),
                alt.Tooltip("Holding:N", title="Holding"),
            ],
        )
        .properties(height=430, title="CCASS 中央結算持股分佈圖")
    )
    if align_price:
        price_data = build_price_history_chart_data(parsed.price_history_table, "Max")
        concentration_line_data = build_ccass_concentration_line_data(parsed.concentration_table, "Max")
        if not price_data.empty:
            rainbow_dates = pd.to_datetime(chart_data["Date"], errors="coerce").dropna()
            if not rainbow_dates.empty:
                date_min = rainbow_dates.min()
                date_max = rainbow_dates.max()
                price_data = price_data[(price_data["Date"] >= date_min) & (price_data["Date"] <= date_max)]
                if not concentration_line_data.empty:
                    concentration_line_data = concentration_line_data[
                        (concentration_line_data["Date"] >= date_min) & (concentration_line_data["Date"] <= date_max)
                    ]
            price_panel = price_turnover_chart(price_data, "Turnover", height=220)
            if price_panel is not None:
                price_panel = price_panel.properties(title="Price / Daily VWAP / Turnover aligned with CCASS rainbow")
                panels = [price_panel, rainbow_chart]
                concentration_panel = ccass_concentration_chart(concentration_line_data, height=150)
                if concentration_panel is not None:
                    panels.append(concentration_panel)
                combined = alt.vconcat(*panels).resolve_scale(
                    x="shared",
                    y="independent",
                    color="independent",
                    strokeDash="independent",
                )
                st.altair_chart(combined, use_container_width=True)
                if not concentration_line_data.empty:
                    latest_concentration = latest_ccass_concentration_values(concentration_line_data)
                    with st.expander("CCASS 股權集中度最新數值", expanded=True):
                        st.dataframe(
                            latest_concentration.assign(Date=latest_concentration["Date"].dt.strftime("%Y-%m-%d"))[
                                ["Metric", "PercentLabel", "Date"]
                            ],
                            use_container_width=True,
                            hide_index=True,
                        )
            else:
                st.altair_chart(rainbow_chart, use_container_width=True)
        else:
            st.caption("Price history is not available, so only the CCASS rainbow is shown.")
            st.altair_chart(rainbow_chart, use_container_width=True)
    else:
        st.altair_chart(rainbow_chart, use_container_width=True)
    # DT-style broker count (券商數目), derived from the same per-date holdings
    # snapshots the rainbow already fetched - no extra requests.
    if "BrokerCount" in chart_data.columns:
        count_data = (
            chart_data.groupby("Date", as_index=False)["BrokerCount"].max().sort_values("Date")
        )
        count_data = count_data[count_data["BrokerCount"] > 0]
        if not count_data.empty:
            latest_count = int(count_data.iloc[-1]["BrokerCount"])
            st.markdown(f"**CCASS 券商數目**　{latest_count}")
            count_chart = (
                alt.Chart(count_data)
                .mark_line(strokeWidth=2, color="#15803d")
                .encode(
                    x=alt.X("Date:T", title="Date"),
                    y=alt.Y("BrokerCount:Q", title="CCASS 券商數目", scale=alt.Scale(zero=False)),
                    tooltip=[
                        alt.Tooltip("Date:T", title="Date", format="%Y-%m-%d"),
                        alt.Tooltip("BrokerCount:Q", title="券商數目"),
                    ],
                )
                .properties(height=140)
            )
            st.altair_chart(count_chart, use_container_width=True)

    latest_rows = (
        chart_data.sort_values("Date")
        .groupby("Participant", as_index=False)
        .tail(1)
        .sort_values("ParticipantOrder")
        [["Participant", "Stake", "Holding"]]
    )
    fetched_dates = chart_data["Date"].nunique()
    st.caption(f"Fetched {fetched_dates} / {len(dates)} dates. Missing participant rows are treated as 0%.")
    if fetched_dates < len(dates):
        st.info("部分歷史日期抓取失敗已被跳過(來源慢或暫時擋請求)。想補齊可以再撳一次「生成真正 DT 彩虹圖」。")
    with st.expander("Latest legend values", expanded=True):
        st.dataframe(latest_rows, use_container_width=True, hide_index=True)


def compact_fetch_summary(fetch_summary):
    return fetch_summary[
        ["Section", "Status", "Tables found", "Selected table index", "Latest date / data date", "Error"]
    ].rename(columns={"Selected table index": "Selected table", "Latest date / data date": "Latest date"})


def render_events(events: list, name: str, warnings: list, capital: dict | None = None) -> None:
    st.markdown("**財技事件 / Corporate events (Webb-site)**")
    st.caption("派息、拆股/合股、送股、供股等權益事件,含比例(new:old)同除淨日。")
    if events:
        df = pd.DataFrame(events)
        preferred = ["announced", "type", "new_old", "ex_date", "amount", "year_end", "notes"]
        cols = [c for c in preferred if c in df.columns] or list(df.columns)
        st.dataframe(df[cols], use_container_width=True, hide_index=True)
    else:
        st.info("暫無公司事件(或 Webb-site events 頁抓取失敗)。")
    for warning in warnings or []:
        st.warning(warning)

    capital = capital or {}
    share_changes = capital.get("share_changes", [])
    buyback_rows = capital.get("buybacks", [])
    st.markdown("**股本變化 / Share capital changes(同花順 F10)**")
    st.caption("配售新股、行使期權、註銷回購等供給事件,連每個時點嘅已發行股本(百萬股)。配售偵測同股本基數核對用呢個表。")
    if share_changes:
        sdf = pd.DataFrame(share_changes)
        preferred = ["announce_date", "shares_million", "reason", "reason_tags", "change_date"]
        cols = [c for c in preferred if c in sdf.columns] or list(sdf.columns)
        st.dataframe(sdf[cols], use_container_width=True, hide_index=True)
    else:
        st.info("暫無股本變化資料(或 10jqka equity 頁抓取失敗)。")
    if buyback_rows:
        with st.expander(f"股份回購記錄({len(buyback_rows)} 條)", expanded=False):
            st.dataframe(pd.DataFrame(buyback_rows), use_container_width=True, hide_index=True)
    for warning in capital.get("warnings", []):
        st.warning(warning)


def render_officers(officers: list, name: str, warnings: list, managers_f10: list | None = None) -> None:
    st.markdown("**現任高管(同花順 F10)**")
    st.caption("跟公告更新嘅現任名單、任期、報酬;背景簡介喺最右一欄,向右拉先睇到。")
    if managers_f10:
        mdf = pd.DataFrame(managers_f10)
        preferred = ["name", "positions", "tenure_from", "tenure_to", "sex", "age", "education", "salary", "biography"]
        cols = [c for c in preferred if c in mdf.columns] or list(mdf.columns)
        st.dataframe(
            mdf[cols],
            use_container_width=True,
            hide_index=True,
            column_config={
                "name": st.column_config.TextColumn("姓名"),
                "positions": st.column_config.TextColumn("職務", width="medium"),
                "tenure_from": st.column_config.TextColumn("上任"),
                "tenure_to": st.column_config.TextColumn("離任"),
                "sex": st.column_config.TextColumn("性別", width="small"),
                "age": st.column_config.NumberColumn("年齡", width="small"),
                "education": st.column_config.TextColumn("學歷", width="small"),
                "salary": st.column_config.TextColumn("報酬"),
                "biography": st.column_config.TextColumn("背景簡介(右拉查看/點格放大)", width="large"),
            },
        )
    else:
        st.info("暫無同花順 F10 高管資料(或抓取失敗)。")

    for warning in warnings or []:
        st.warning(warning)


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


st.markdown(
    """
    <div class="ccass-hero">
        <h1>📊 Webb-site CCASS Extractor</h1>
        <p>香港股票 CCASS 持股 / 異動 / 集中度 · 公司事件 · 董事高管 — 研究用途,非投資建議,請避免高頻抓取。</p>
    </div>
    """,
    unsafe_allow_html=True,
)

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
    announcement_years = st.selectbox("HKEX announcements period", [1, 2], index=0, format_func=lambda value: f"{value} year" if value == 1 else f"{value} years")
    headless = st.toggle("Playwright headless", value=env_bool("PLAYWRIGHT_HEADLESS", True))
    fetch_clicked = st.button("Fetch Webb-site Data", type="primary", use_container_width=True)

if "results" not in st.session_state:
    st.session_state.results = None
    st.session_state.lookup = empty_lookup()
    st.session_state.manual_issue_id = ""

if "hkex_announcements" not in st.session_state:
    st.session_state.hkex_announcements = empty_announcements()

if "events" not in st.session_state:
    st.session_state.events = {"records": [], "name": "", "warnings": []}
if "officers" not in st.session_state:
    st.session_state.officers = {"records": [], "name": "", "warnings": []}
if "capital" not in st.session_state:
    st.session_state.capital = {"share_changes": [], "buybacks": [], "warnings": []}

if fetch_clicked:
    raw_input = user_input.strip()
    st.session_state.results = None
    st.session_state.lookup = empty_lookup()
    st.session_state.hkex_announcements = empty_announcements(period_years=int(announcement_years))
    st.session_state.events = {"records": [], "name": "", "warnings": []}
    st.session_state.officers = {"records": [], "name": "", "warnings": []}
    st.session_state.capital = {"share_changes": [], "buybacks": [], "warnings": []}

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
            with st.status("Fetching Company / Holdings / Changes / Big Changes / Concentration / Price History...", expanded=True) as status:
                results = fetch_all(issue_id, stock_code=stock_code, timeout=int(timeout), headless=headless)
                for section in SECTIONS:
                    result = results.get(section)
                    if result:
                        st.write(f"{section}: {'success' if result.ok else 'failed'}, tables={len(result.tables)}")
                status.update(label="Fetch complete", state="complete")
            if stock_code:
                with st.status("Fetching HKEX announcements...", expanded=True) as status:
                    announcements = fetch_announcements(stock_code, period_years=int(announcement_years), timeout=int(timeout))
                    if announcements.ok:
                        st.write(f"HKEX announcements: {len(announcements.table)} rows loaded, HKEX total count={announcements.total_count}")
                    else:
                        st.warning(announcements.error)
                    status.update(label="HKEX announcement fetch complete", state="complete")
                st.session_state.hkex_announcements = announcements

            with st.status("Fetching Webb-site events & officers...", expanded=True) as status:
                events_records, events_name, events_warn = [], "", []
                try:
                    ev = fetch_with_requests("Events", events_url(issue_id), timeout=min(int(timeout), 20))
                    if ev.html:
                        events_records = parse_events_html(ev.html)
                        events_name = parse_events_name(ev.html)
                        st.write(f"Events: {len(events_records)} rows")
                    else:
                        events_warn.append(f"Events fetch failed: {ev.error_type}: {ev.error_message}")
                except Exception as exc:  # pragma: no cover - defensive
                    events_warn.append(f"Events error: {type(exc).__name__}: {exc}")

                # Webb-site officers are no longer shown on the page (data frozen
                # 2025-03-31); the F10 managers below are the live source.
                officers_records, officers_name, officers_warn = [], "", []

                managers_records = []
                try:
                    f10 = fetch_with_requests("F10 Managers", f10_managers_url(stock_code or ""), timeout=min(int(timeout), 20)) if stock_code else None
                    if f10 is not None and f10.html:
                        managers_records = parse_f10_managers_html(f10.html)
                        st.write(f"10jqka 高管: {len(managers_records)} 人")
                    elif f10 is not None:
                        officers_warn.append(f"10jqka managers fetch failed: {f10.error_type}: {f10.error_message}")
                except Exception as exc:  # pragma: no cover - defensive
                    officers_warn.append(f"10jqka managers error: {type(exc).__name__}: {exc}")

                share_changes, buyback_rows, capital_warn = [], [], []
                try:
                    eq = fetch_with_requests("F10 Equity", f10_equity_url(stock_code or ""), timeout=min(int(timeout), 20)) if stock_code else None
                    if eq is not None and eq.html:
                        share_changes = parse_f10_share_changes(eq.html)
                        buyback_rows = parse_f10_buybacks(eq.html)
                        st.write(f"10jqka 股本變化: {len(share_changes)} 條 · 回購: {len(buyback_rows)} 條")
                    elif eq is not None:
                        capital_warn.append(f"10jqka equity fetch failed: {eq.error_type}: {eq.error_message}")
                except Exception as exc:  # pragma: no cover - defensive
                    capital_warn.append(f"10jqka equity error: {type(exc).__name__}: {exc}")
                st.session_state.capital = {"share_changes": share_changes, "buybacks": buyback_rows, "warnings": capital_warn}

                st.session_state.events = {"records": events_records, "name": events_name, "warnings": events_warn}
                st.session_state.officers = {
                    "records": officers_records,
                    "name": officers_name,
                    "warnings": officers_warn,
                    "managers_f10": managers_records,
                }
                status.update(label="Events & officers fetch complete", state="complete")

            st.session_state.results = results
            st.session_state.lookup = lookup

results = st.session_state.results
lookup = st.session_state.lookup
hkex_announcements = st.session_state.hkex_announcements

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
    columns = st.columns(len(SECTIONS))
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
report = build_report(parsed, results, hkex_announcements=hkex_announcements)
fetch_summary = build_fetch_summary(parsed, results)
json_ready = parsed_to_json_ready(parsed, results)

with meta_cols[1]:
    st.caption("Stock name")
    st.markdown(f"**{parsed.stock_name or '-'}**")

st.divider()

# KPI summary strip (DT-style)
kpi = st.columns(5)
kpi[0].metric("Stock", parsed.stock_code or "-")
kpi[1].metric("CCASS %", parsed.total_in_ccass_pct or "-")
kpi[2].metric("Top 5 (of issued)", parsed.top5_cumulative_pct or "-")
kpi[3].metric("Top 10 (of issued)", parsed.top10_cumulative_pct or "-")
kpi[4].metric("Largest participant", parsed.largest_participant or "-")
st.caption(
    f"📅 數據截至:Holdings {parsed.holdings_data_date or '-'} · Changes(交易日){parsed.changes_trading_date or '-'} · "
    "CCASS 係 T+2 結算數據 — 某日嘅買賣要兩個交易日後先反映落持倉;最近兩個交易日嘅變動未必已包含在內。"
)

events_state = st.session_state.get("events", {"records": [], "name": "", "warnings": []})
officers_state = st.session_state.get("officers", {"records": [], "name": "", "warnings": []})

st.divider()
st.markdown('<div id="price-turnover"></div>', unsafe_allow_html=True)
render_price_history(parsed)

if parsed.stock_code:
    hkex_code = str(int(parsed.stock_code)) if str(parsed.stock_code).isdigit() else parsed.stock_code
    st.link_button(
        "Open HKEX quote",
        f"https://www.hkex.com.hk/Market-Data/Securities-Prices/Equities/Equities-Quote?sym={hkex_code}&sc_lang=zh-hk",
    )

st.divider()
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
    [DT Rainbow](#dt-rainbow) | [HKEX Announcements](#hkex-announcements) |
    [財技事件 Events](#corporate-events) | [董事高管 Officers](#officers) |
    [Price & Turnover](#price-turnover) |
    [Company](#company) | [Holdings](#holdings) | [Changes](#changes) |
    [Big Changes](#big-changes) | [Concentration](#concentration) | [Price History](#price-history) |
    [Raw Previews](#raw-table-previews) | [Copy for ChatGPT](#copy-for-chatgpt) |
    [Downloads](#download-files)
    """
)

st.divider()
st.markdown('<div id="dt-rainbow"></div>', unsafe_allow_html=True)
render_dt_participant_rainbow(parsed, timeout, headless)

st.divider()
st.markdown('<div id="hkex-announcements"></div>', unsafe_allow_html=True)
render_hkex_announcements(hkex_announcements)

st.divider()
st.markdown('<div id="corporate-events"></div>', unsafe_allow_html=True)
render_events(
    events_state.get("records", []),
    events_state.get("name", ""),
    events_state.get("warnings", []),
    st.session_state.get("capital", {}),
)

st.divider()
st.markdown('<div id="officers"></div>', unsafe_allow_html=True)
render_officers(
    officers_state.get("records", []),
    officers_state.get("name", ""),
    officers_state.get("warnings", []),
    officers_state.get("managers_f10", []),
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
with st.expander("Show concentration band chart (not DT rainbow)", expanded=False):
    render_rainbow_chart(parsed)
render_concentration_change(parsed)

st.divider()
st.markdown('<div id="price-history"></div>', unsafe_allow_html=True)
section = "Price History"
parse = parsed.section_parses.get(section)
render_section(
    section,
    results.get(section),
    parse.selected_table_index if parse else "",
    parsed.price_history_table,
    (
        f"Latest date: {parsed.price_history_latest_date or 'not available'} | "
        f"Close: {parsed.latest_price or 'not available'} | "
        f"Volume: {parsed.latest_price_volume or 'not available'} | "
        f"Turnover: {parsed.latest_price_turnover or 'not available'} | "
        f"VWAP: {parsed.latest_price_vwap or 'not available'}"
    ),
)

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
