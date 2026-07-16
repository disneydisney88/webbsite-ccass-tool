"""Parse Webb-site officers/directors (dbpub/officers.asp?p=<org_id>).

The officers page lists a company's board and management across one or more
"opltable" tables (e.g. "Main board", "Manager/adviser/other"). Each row has a
person link (positions.asp?p=<person_id>), a sex symbol, age, a position with a
short code and full title in nested spans, and appointment / resignation dates.

Officers are keyed by the *organisation* id (p=), which differs from the CCASS
issue id (i=). extract_org_id_from_html recovers it from any Webb-site org page
(the shared nav links to orgdata.asp?p=<org_id>). Nothing is invented: fields the
source omits are returned as null.
"""

from __future__ import annotations

import re
from typing import Any

from utils.fetcher import BASE_URL


def officers_url(org_id: str, snapshot_date: str | None = None) -> str:
    url = f"{BASE_URL}/dbpub/officers.asp?p={org_id}"
    if snapshot_date:
        url += f"&d={snapshot_date}"
    return url


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _to_int(text: str) -> int | None:
    match = re.search(r"-?\d+", text or "")
    return int(match.group(0)) if match else None


def extract_org_id_from_html(html: str) -> str:
    """Recover the organisation id (p=) from any Webb-site org page's nav."""
    if not html:
        return ""
    match = re.search(r"(?:orgdata|officers|overlap|pay|advisers|docs)\.asp\?p=(\d+)", html, flags=re.I)
    return match.group(1) if match else ""


def parse_officers_name(html: str) -> str:
    from bs4 import BeautifulSoup

    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    heading = soup.find("h2")
    return _clean(heading.get_text(" ", strip=True)) if heading else ""


def parse_shutdown_notice(html: str) -> str | None:
    from bs4 import BeautifulSoup

    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    box = soup.find(class_="letterbox")
    if not box:
        return None
    text = _clean(box.get_text(" ", strip=True))
    return text or None


def _link_url(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"{BASE_URL}{href}"
    return f"{BASE_URL}/dbpub/{href}"


def _position_parts(cell) -> tuple[str | None, str | None]:
    """Split a position cell into (short code, full title).

    Markup is <span class="info">ED<span>Executive Director</span></span>: the
    outer span's direct text is the code, the nested span is the full title.
    """
    info = cell.find("span", class_="info")
    if not info:
        text = _clean(cell.get_text(" ", strip=True))
        return (text or None, None)
    inner = info.find("span")
    full = _clean(inner.get_text(" ", strip=True)) if inner else None
    code = _clean("".join(str(node) for node in info.find_all(string=True, recursive=False)))
    return (code or None, full or None)


def _column_map(header_row) -> dict[int, str]:
    keys: dict[int, str] = {}
    for idx, cell in enumerate(header_row.find_all(["th", "td"])):
        label = _clean(cell.get_text(" ", strip=True)).lower()
        if "name" in label:
            keys[idx] = "name"
        elif "age" in label:
            keys[idx] = "age"
        elif "position" in label:
            keys[idx] = "position"
        elif "from" in label:
            keys[idx] = "from_date"
        elif "until" in label:
            keys[idx] = "until_date"
        elif idx == 0:
            keys[idx] = "rank"
        else:
            keys[idx] = "sex"
    return keys


def parse_officers_html(html: str) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")

    officers: list[dict[str, Any]] = []
    for table in soup.find_all("table", class_="opltable"):
        heading = table.find_previous("h3")
        group = _clean(heading.get_text(" ", strip=True)) if heading else None
        rows = table.find_all("tr")
        if not rows:
            continue
        colmap = _column_map(rows[0])

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            record: dict[str, Any] = {"table_group": group}
            for idx, cell in enumerate(cells):
                key = colmap.get(idx)
                if key is None:
                    continue
                if key == "name":
                    link = cell.find("a", href=re.compile(r"positions\.asp\?p=\d+"))
                    if link:
                        record["name"] = _clean(link.get_text(" ", strip=True))
                        match = re.search(r"p=(\d+)", link["href"])
                        record["person_id"] = match.group(1) if match else None
                        record["person_url"] = _link_url(link["href"])
                    else:
                        record["name"] = _clean(cell.get_text(" ", strip=True)) or None
                elif key == "position":
                    code, full = _position_parts(cell)
                    record["position_code"] = code
                    record["position"] = full
                elif key == "age":
                    record["age"] = _to_int(cell.get_text(" ", strip=True))
                elif key == "sex":
                    record["sex"] = _clean(cell.get_text(" ", strip=True)) or None
                elif key == "from_date":
                    record["from_date"] = _clean(cell.get_text(" ", strip=True)) or None
                elif key == "until_date":
                    record["until_date"] = _clean(cell.get_text(" ", strip=True)) or None
                elif key == "rank":
                    record["rank"] = _to_int(cell.get_text(" ", strip=True))
            # Continuation rows (position changes) have no name link; skip them so
            # each returned record is a named person.
            if not record.get("name"):
                continue
            record["is_current"] = not bool(record.get("until_date"))
            officers.append(record)
    return officers
