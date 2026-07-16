"""Parse Webb-site corporate events (dbpub/events.asp?i=<issue_id>).

The events page lists capital actions and distributions in a single HTML table:
dividends, splits/consolidations, bonus issues, rights, etc. Each row's Type
links to an eventdets.asp?e=<id> detail page. This module locates that table
(the page also contains an unrelated listing table) and returns clean records
without inventing any field the source does not provide.
"""

from __future__ import annotations

import re
from typing import Any

from utils.fetcher import BASE_URL

# (needle found in the source header, clean output key)
_HEADER_ALIASES = [
    ("announced", "announced"),
    ("year", "year_end"),
    ("type", "type"),
    ("amount", "amount"),
    ("value", "value_in_quote_ccy"),
    ("new", "new_old"),
    ("ex-date", "ex_date"),
    ("ex date", "ex_date"),
    ("exdate", "ex_date"),
    ("distri", "distribution"),
    ("note", "notes"),
]


def events_url(issue_id: str) -> str:
    return f"{BASE_URL}/dbpub/events.asp?i={issue_id}"


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _header_key(header: str) -> str:
    cleaned = _clean_text(header).lower()
    for needle, key in _HEADER_ALIASES:
        if needle in cleaned:
            return key
    fallback = re.sub(r"[^a-z0-9]+", "_", cleaned).strip("_")
    return fallback or "column"


def _detail_url(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"{BASE_URL}{href}"
    return f"{BASE_URL}/dbpub/{href}"


def parse_events_name(html: str) -> str:
    """Best-effort company name from the events page heading."""
    from bs4 import BeautifulSoup

    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    heading = soup.find("h2")
    return _clean_text(heading.get_text(" ", strip=True)) if heading else ""


def parse_events_html(html: str) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")

    target = None
    for table in soup.find_all("table"):
        header_text = " ".join(_clean_text(th.get_text(" ", strip=True)).lower() for th in table.find_all("th"))
        if "announced" in header_text and "type" in header_text:
            target = table
            break
    if target is None:
        return []

    rows = target.find_all("tr")
    if not rows:
        return []
    keys = [_header_key(cell.get_text(" ", strip=True)) for cell in rows[0].find_all(["th", "td"])]

    records: list[dict[str, Any]] = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        values = [_clean_text(cell.get_text(" ", strip=True)) for cell in cells]
        if not any(values):
            continue
        record: dict[str, Any] = {}
        for key, value in zip(keys, values):
            record[key] = value or None
        link = row.find("a", href=re.compile(r"eventdets\.asp\?e=\d+"))
        if link and link.get("href"):
            match = re.search(r"e=(\d+)", link["href"])
            record["event_id"] = match.group(1) if match else None
            record["event_details_url"] = _detail_url(link["href"])
        records.append(record)
    return records
