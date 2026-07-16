"""Parse 同花順 F10 HK manager pages (basic.10jqka.com.cn/HK<code4>/manager.html).

Each executive is one server-rendered <table class="m_table ggintro"> with the
name (h3), positions, current tenure (本届任期), sex/age/education, salary
(报酬) and a full biography paragraph (mainintro). The biography is kept
verbatim per user requirement. This complements Webb-site officers data, whose
updates froze on 2025-03-31: the F10 page carries appointments after that date.

Fields the source omits ("--", blanks) are returned as null - never invented.
"""

from __future__ import annotations

import re
from typing import Any

F10_BASE_URL = "https://basic.10jqka.com.cn"


def f10_stock_slug(stock_code: str) -> str:
    """00550 -> HK0550, 06162 -> HK6162. Codes above 9999 keep all digits."""
    digits = re.sub(r"\D", "", stock_code or "")
    if not digits:
        return ""
    value = int(digits)
    return f"HK{value:04d}" if value <= 9999 else f"HK{value}"


def f10_managers_url(stock_code: str) -> str:
    slug = f10_stock_slug(stock_code)
    return f"{F10_BASE_URL}/{slug}/manager.html" if slug else ""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _null_if_dash(text: str | None) -> str | None:
    cleaned = _clean(text or "")
    return None if cleaned in {"", "--", "-"} else cleaned


def parse_f10_stock_name(html: str) -> str:
    from bs4 import BeautifulSoup

    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    hidden = soup.find("input", id="stockName")
    if hidden and hidden.get("value"):
        return _clean(hidden["value"])
    title = soup.find("title")
    if title:
        match = re.match(r"([^(（]+)", _clean(title.get_text()))
        if match:
            return match.group(1).strip()
    return ""


def _parse_intro_cell(text: str) -> dict[str, Any]:
    """'男 52 本科' / '女  硕士' / '男 52 ' -> sex, age, education."""
    cleaned = _clean(text)
    result: dict[str, Any] = {"sex": None, "age": None, "education": None}
    if not cleaned:
        return result
    parts = cleaned.split(" ")
    for part in parts:
        if part in {"男", "女"}:
            result["sex"] = part
        elif part.isdigit():
            result["age"] = int(part)
        elif part:
            result["education"] = part
    return result


def _parse_tenure(text: str) -> tuple[str | None, str | None, bool]:
    """'本届任期：2026-01-20 至今' -> (from, to, is_current)."""
    cleaned = _clean(text).replace("本届任期：", "").replace("本届任期:", "")
    match = re.search(r"(\d{4}-\d{2}-\d{2})", cleaned)
    tenure_from = match.group(1) if match else None
    if "至今" in cleaned:
        return tenure_from, None, True
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", cleaned)
    tenure_to = dates[1] if len(dates) > 1 else None
    return tenure_from, tenure_to, tenure_to is None


def parse_f10_managers_html(html: str) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")

    managers: list[dict[str, Any]] = []
    for table in soup.find_all("table", class_="ggintro"):
        name_cell = table.find("td", class_="title")
        name = _clean(name_cell.get_text(" ", strip=True)) if name_cell else ""
        if not name:
            continue

        jobs_cell = table.find("td", class_="jobs")
        positions = _clean(jobs_cell.get_text(" ", strip=True)) if jobs_cell else None

        date_cell = table.find("td", class_="date")
        tenure_from, tenure_to, is_current = _parse_tenure(date_cell.get_text(" ", strip=True) if date_cell else "")

        intro_cell = table.find("td", class_="intro")
        intro = _parse_intro_cell(intro_cell.get_text(" ", strip=True) if intro_cell else "")

        salary_cell = table.find("td", class_="salary")
        salary = None
        if salary_cell:
            salary = _null_if_dash(_clean(salary_cell.get_text(" ", strip=True)).replace("报酬：", "").replace("报酬:", ""))

        bio_cell = table.find("td", class_="mainintro")
        biography = _clean(bio_cell.get_text(" ", strip=True)) if bio_cell else None

        managers.append(
            {
                "name": name,
                "positions": positions or None,
                "tenure_from": tenure_from,
                "tenure_to": tenure_to,
                "is_current": is_current,
                "sex": intro["sex"],
                "age": intro["age"],
                "education": intro["education"],
                "salary": salary,
                "biography": biography or None,
            }
        )
    return managers
