"""Historical CCASS holdings snapshots and two-date diffs (handover 3.2).

Webb-site serves a full participant table for any past date via
choldings.asp?d=<YYYY-MM-DD>&i=<issue_id>. parse_holdings_snapshot extracts all
participant rows from such a page; diff_snapshots compares two dates and
reports per-participant share/stake changes, new and exited participants, and
net flow aggregated by participant category (retail / bank / boutique /
intl_broker / unknown).
"""

from __future__ import annotations

import re
from typing import Any

from utils.fetcher import issue_urls
from utils.participants import categorize
from utils.parser import to_number

PARTICIPANT_ID_RE = re.compile(r"^[A-Ca-c]\d{5}$")


def snapshot_url(issue_id: str, date: str) -> str:
    base = issue_urls(issue_id)["Holdings"]
    return base.replace(f"?i={issue_id}", f"?d={date}&i={issue_id}")


def _pick_column(columns, needles: list[str]) -> str | None:
    for needle in needles:
        for column in columns:
            if needle.lower() in str(column).lower():
                return column
    return None


def parse_holdings_snapshot(tables) -> list[dict[str, Any]]:
    """Extract every participant row from a holdings page's parsed tables."""
    best: list[dict[str, Any]] = []
    for table in tables or []:
        if table is None or getattr(table, "empty", True):
            continue
        id_col = _pick_column(table.columns, ["ccass id", "participant id"]) or _pick_column(table.columns, ["id"])
        holding_col = _pick_column(table.columns, ["holding"])
        stake_col = _pick_column(table.columns, ["stake %", "stake", "%"])
        name_col = _pick_column(table.columns, ["name", "participant"])
        if not id_col or not holding_col:
            continue
        rows: list[dict[str, Any]] = []
        for _, row in table.iterrows():
            ccass_id = str(row.get(id_col, "") or "").strip()
            if not PARTICIPANT_ID_RE.match(ccass_id):
                continue
            holding = to_number(row.get(holding_col))
            rows.append(
                {
                    "ccass_id": ccass_id.upper(),
                    "name": str(row.get(name_col, "") or "").strip() if name_col else "",
                    "holding": int(holding) if holding is not None else None,
                    "stake_pct": to_number(row.get(stake_col)) if stake_col else None,
                }
            )
        if len(rows) > len(best):
            best = rows
    return best


def _top_stake_sum(rows: list[dict[str, Any]], count: int) -> float | None:
    stakes = sorted((r["stake_pct"] for r in rows if r.get("stake_pct") is not None), reverse=True)
    if not stakes:
        return None
    return round(sum(stakes[:count]), 4)


def diff_snapshots(
    rows_a: list[dict[str, Any]],
    rows_b: list[dict[str, Any]],
    date_a: str,
    date_b: str,
) -> dict[str, Any]:
    """Compare two holdings snapshots (a = earlier, b = later)."""
    map_a = {row["ccass_id"]: row for row in rows_a}
    map_b = {row["ccass_id"]: row for row in rows_b}

    changes: list[dict[str, Any]] = []
    category_net: dict[str, int] = {}
    for ccass_id in sorted(set(map_a) | set(map_b)):
        a = map_a.get(ccass_id)
        b = map_b.get(ccass_id)
        holding_a = (a or {}).get("holding") or 0
        holding_b = (b or {}).get("holding") or 0
        change_shares = holding_b - holding_a
        stake_a = (a or {}).get("stake_pct")
        stake_b = (b or {}).get("stake_pct")
        if a is None:
            status = "new"
        elif b is None:
            status = "exited"
        elif change_shares > 0:
            status = "increased"
        elif change_shares < 0:
            status = "decreased"
        else:
            status = "unchanged"
        name = (b or a or {}).get("name", "")
        category = categorize(ccass_id, name)
        category_net[category] = category_net.get(category, 0) + change_shares
        if status == "unchanged":
            continue
        changes.append(
            {
                "ccass_id": ccass_id,
                "name": name,
                "category": category,
                "holding_a": (a or {}).get("holding"),
                "holding_b": (b or {}).get("holding"),
                "change_shares": change_shares,
                "stake_a_pct": stake_a,
                "stake_b_pct": stake_b,
                "change_stake_points": round(stake_b - stake_a, 4) if stake_a is not None and stake_b is not None else None,
                "status": status,
            }
        )

    changes.sort(key=lambda item: abs(item["change_shares"]), reverse=True)
    return {
        "date_a": date_a,
        "date_b": date_b,
        "participants_a": len(rows_a),
        "participants_b": len(rows_b),
        "new_participants": [c["ccass_id"] for c in changes if c["status"] == "new"],
        "exited_participants": [c["ccass_id"] for c in changes if c["status"] == "exited"],
        "top5_stake_a_pct": _top_stake_sum(rows_a, 5),
        "top5_stake_b_pct": _top_stake_sum(rows_b, 5),
        "top10_stake_a_pct": _top_stake_sum(rows_a, 10),
        "top10_stake_b_pct": _top_stake_sum(rows_b, 10),
        "category_net_change_shares": category_net,
        "changes": changes,
    }
