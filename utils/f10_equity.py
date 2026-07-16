"""Parse 同花順 F10 HK equity pages (basic.10jqka.com.cn/HK<code4>/equity.html).

Two server-rendered tables matter for 財技 analysis:

- 股本变化情况 (div#change): issued-share history with announce date, share
  count in millions, reason (配售新股 / 行使购股权 / 注销回购股份 / ...) and
  effective date. This is the only in-tool source of placements with dates and
  the share base at each point in time.
- 股份回购 (div#purchase): per-day buyback records with amounts and price range.

Each share-change row also gets canonical `reason_tags` (placement,
option_exercise, buyback_cancellation, ...) derived by keyword matching so
downstream analysis can filter without parsing Chinese text. "--" fields are
null; nothing is invented.
"""

from __future__ import annotations

import re
from typing import Any

from utils.f10_managers import F10_BASE_URL, f10_stock_slug

REASON_TAG_KEYWORDS = [
    ("placement", ("配售新股", "配售股份")),
    ("option_exercise", ("行使购股权", "行使股份期权", "行使認股權", "行使认股权")),
    ("buyback_cancellation", ("注销回购", "註銷回購")),
    ("ipo", ("首发上市", "首發上市")),
    ("consideration_issue", ("发行代价股份", "發行代價股份")),
    ("rights_issue", ("供股",)),
    ("open_offer", ("公开发售", "公開發售")),
    ("bonus_issue", ("红股", "紅股", "送股")),
    ("consolidation", ("合股", "并股", "合併股份", "股份合并")),
    ("subdivision", ("拆细", "拆細", "股份拆分")),
    ("capital_reduction", ("削减股本", "削減股本", "股本削减")),
]


def f10_equity_url(stock_code: str) -> str:
    slug = f10_stock_slug(stock_code)
    return f"{F10_BASE_URL}/{slug}/equity.html" if slug else ""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _null_if_dash(text: str | None) -> str | None:
    cleaned = _clean(text or "")
    return None if cleaned in {"", "--", "-"} else cleaned


def _to_float(text: str | None) -> float | None:
    cleaned = _null_if_dash(text)
    if cleaned is None:
        return None
    try:
        return float(cleaned.replace(",", ""))
    except ValueError:
        return None


def reason_tags(reason: str | None) -> list[str]:
    if not reason:
        return []
    return [tag for tag, needles in REASON_TAG_KEYWORDS if any(needle in reason for needle in needles)]


def _box_table(soup, box_id: str, heading_needle: str):
    box = soup.find(id=box_id)
    if box is None:
        for candidate in soup.find_all(class_="m_box"):
            heading = candidate.find("h2")
            if heading and heading_needle in heading.get_text():
                box = candidate
                break
    return box.find("table") if box else None


def parse_f10_share_changes(html: str) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    table = _box_table(soup, "change", "股本变化")
    if table is None:
        return []

    changes: list[dict[str, Any]] = []
    for row in table.find_all("tr"):
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
        if len(cells) < 4 or not re.match(r"\d{4}-\d{2}-\d{2}", cells[0] or ""):
            continue
        shares_million = _to_float(cells[1])
        reason = _null_if_dash(cells[2])
        changes.append(
            {
                "announce_date": _null_if_dash(cells[0]),
                "shares_million": shares_million,
                "shares_approx": int(shares_million * 1_000_000) if shares_million is not None else None,
                "reason": reason,
                "reason_tags": reason_tags(reason),
                "change_date": _null_if_dash(cells[3]),
            }
        )
    return changes


def parse_f10_buybacks(html: str) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    table = _box_table(soup, "purchase", "股份回购")
    if table is None:
        return []

    buybacks: list[dict[str, Any]] = []
    for row in table.find_all("tr"):
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
        if len(cells) < 8:
            continue
        if not re.match(r"\d{4}-\d{2}-\d{2}", cells[1] or ""):
            continue
        buybacks.append(
            {
                "announce_date": _null_if_dash(cells[0]),
                "buyback_date": _null_if_dash(cells[1]),
                "amount_wan": _to_float(cells[2]),
                "shares_wan": _to_float(cells[3]),
                "high_price": _to_float(cells[4]),
                "low_price": _to_float(cells[5]),
                "method": _null_if_dash(cells[6]),
                "currency": _null_if_dash(cells[7]),
            }
        )
    return buybacks


def latest_share_capital(changes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Most recent share-capital snapshot (rows are newest-first on the page,
    but sort defensively by change_date)."""
    dated = [c for c in changes if c.get("change_date") and c.get("shares_million") is not None]
    if not dated:
        return None
    latest = max(dated, key=lambda c: c["change_date"])
    return {
        "shares_million": latest["shares_million"],
        "shares_approx": latest["shares_approx"],
        "as_of": latest["change_date"],
        "reason": latest["reason"],
        "note": "Share count is reported in millions (2dp) by the source, so shares_approx is precise to ~10,000 shares.",
    }
