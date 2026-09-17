"""Fill the persistent light stock-response cache from a non-Render machine.

The script deliberately runs the existing requests-only ``hybrid_light``
fetcher locally, then writes only verified responses to Turso.  It never calls
Render to perform a cache miss, so free-plan cold starts are not part of the
warming path.

Examples (PowerShell):
  $env:TURSO_DATABASE_URL = 'libsql://...'
  $env:TURSO_AUTH_TOKEN = '...'
  python scripts/warm_ccass_cache.py --codes 01825 02048 08059 01592 03301
  python scripts/warm_ccass_cache.py --panel data/eod/radar_eod_panel_full.csv --sleep 3
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api import build_stock_payload  # noqa: E402
from utils.turso_db import put_api_stock_cache, turso_is_configured  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codes", nargs="*", default=[], help="Five-digit code5 values.")
    parser.add_argument("--codes-file", type=Path, help="Text/CSV file containing code5 values.")
    parser.add_argument("--panel", type=Path, help="Panel CSV; warms its unique code5 values.")
    parser.add_argument("--sleep", type=float, default=3.0, help="Seconds between upstream stocks.")
    parser.add_argument("--timeout", type=int, default=30, help="Per-stock Webb-site request budget.")
    parser.add_argument("--start-after", default="", help="Resume after this code5 (exclusive).")
    parser.add_argument("--limit", type=int, default=0, help="Optional maximum number of stocks.")
    parser.add_argument("--dry-run", action="store_true", help="List codes without fetching or writing.")
    return parser.parse_args()


def normalise_code(value: object) -> str:
    text = str(value or "").strip().lower().replace(".hk", "")
    digits = "".join(char for char in text if char.isdigit())
    return digits.zfill(5) if digits else ""


def read_codes_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return []
    header = [str(item).strip().lower() for item in rows[0]]
    index = header.index("code5") if "code5" in header else 0
    return [normalise_code(row[index]) for row in rows[1:] if len(row) > index]


def collect_codes(args: argparse.Namespace) -> list[str]:
    values = list(args.codes)
    if args.codes_file:
        values.extend(read_codes_file(args.codes_file))
    if args.panel:
        values.extend(read_codes_file(args.panel))
    codes = sorted({code for code in (normalise_code(item) for item in values) if code})
    if args.start_after:
        marker = normalise_code(args.start_after)
        codes = [code for code in codes if code > marker]
    if args.limit > 0:
        codes = codes[: args.limit]
    return codes


def main() -> int:
    args = parse_args()
    codes = collect_codes(args)
    if not codes:
        raise SystemExit("No code5 supplied. Use --codes, --codes-file, or --panel.")
    if args.dry_run:
        print(json.dumps({"count": len(codes), "codes": codes}, ensure_ascii=False))
        return 0
    if not turso_is_configured():
        raise SystemExit(
            "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are required; "
            "the local warm job refuses to fall back to Render."
        )

    started = time.monotonic()
    ok_count = failed_count = 0
    for index, code in enumerate(codes, start=1):
        item_started = time.monotonic()
        try:
            payload = build_stock_payload(
                stock_code=code,
                timeout=args.timeout,
                source_preference="hybrid_light",
                include_price_history=False,
                headless=False,
                bypass_cache=True,
            )
            errors = payload.get("errors") or []
            concentration = payload.get("concentration") or {}
            big_changes = payload.get("big_changes") or []
            verified = bool(payload.get("ok")) and not errors and bool(
                concentration.get("records") or big_changes
            )
            if not verified:
                failed_count += 1
                print(json.dumps({"index": index, "code": code, "status": "FETCH_FAIL", "errors": errors[:3]}, ensure_ascii=False), flush=True)
            else:
                cache_key = f"stock:{code}:light:v1"
                if not put_api_stock_cache(cache_key, payload):
                    failed_count += 1
                    print(json.dumps({"index": index, "code": code, "status": "TURSO_WRITE_FAIL"}), flush=True)
                else:
                    ok_count += 1
                    print(json.dumps({"index": index, "code": code, "status": "OK", "seconds": round(time.monotonic() - item_started, 1)}), flush=True)
        except Exception as exc:  # one stock must not discard completed cache entries
            failed_count += 1
            print(json.dumps({"index": index, "code": code, "status": "FETCH_FAIL", "error_type": type(exc).__name__}), flush=True)
        if index < len(codes):
            time.sleep(max(0.0, args.sleep))

    print(json.dumps({"total": len(codes), "ok": ok_count, "failed": failed_count, "elapsed_s": round(time.monotonic() - started, 1)}), flush=True)
    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
