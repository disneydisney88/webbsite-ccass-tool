"""Classify CCASS participants into flow categories.

Categories (retail / bank / boutique / intl_broker / unknown) help read whether
a holding change looks like retail distribution, custody movement, or warehouse
accumulation. The mapping lives in config/participant_categories.json so it can
be extended without code changes.

Lookup order: exact CCASS ID, then case-insensitive name-keyword substring,
else "unknown".
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "participant_categories.json",
)

VALID_CATEGORIES = {"retail", "bank", "boutique", "intl_broker", "unknown"}


@lru_cache(maxsize=1)
def _load_mapping() -> tuple[dict[str, str], tuple[tuple[str, str], ...]]:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}, ()
    by_id = {str(key).strip().upper(): value for key, value in data.get("by_ccass_id", {}).items()}
    by_keyword = tuple((str(key).strip().upper(), value) for key, value in data.get("by_name_keyword", {}).items())
    return by_id, by_keyword


def categorize(ccass_id: str | None = None, name: str | None = None) -> str:
    by_id, by_keyword = _load_mapping()
    cleaned_id = str(ccass_id).strip().upper() if ccass_id else ""
    if cleaned_id:
        category = by_id.get(cleaned_id)
        if category:
            return category
    if name:
        upper_name = str(name).upper()
        for keyword, category in by_keyword:
            if keyword and keyword in upper_name:
                return category
    # CCASS ID prefix fallback: C-prefixed participants are custodians (banks);
    # explicit ID/keyword mappings above take precedence (e.g. Citibank ->
    # intl_broker).
    if cleaned_id.startswith("C"):
        return "bank"
    return "unknown"
