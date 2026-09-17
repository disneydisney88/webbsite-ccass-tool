"""Fill the persistent light stock-response cache from a non-Render machine.

P6 v2 (2026-09-17): direct Webb-site mirror fetching with a pre-resolved
issue-id map and worker concurrency.  Never calls Render; free-plan cold
starts are not part of the warming path.

Changes vs v1:
  * Secrets are loaded from ``_secrets/.env`` (Google Drive, gitignored),
    falling back to ``%USERPROFILE%\\.stockscan\\.env``; already-set
    environment variables always win.  Values are never printed.
  * A batch pre-pass resolves ``code5 -> issue_id`` once per stock and
    persists it in the local snapshot DB ``stock_map``, so the per-stock
    payload build never re-fetches ``orgdata``.
  * ``--workers`` fetches stocks concurrently (default 4) with a dispatch
    sleep to respect the mirror; each stock still writes the same
    ``stock:{code}:light:v1`` Turso payload as before.
  * The Yahoo price-history side fetch is disabled for warming (it was a
    ~30s hang per stock on a fresh snapshot DB and its result is unused in
    light mode).

Examples (PowerShell):
  python scripts/warm_ccass_cache.py --codes 01825 02048 08059 01592 03301
  python scripts/warm_ccass_cache.py --panel "G:\\我的雲端硬碟\\STOCKSCAN\\data\\eod\\radar_eod_panel_full.csv" --sleep 1 --start-after 00065
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api import build_stock_payload  # noqa: E402
from utils.turso_db import put_api_stock_cache, turso_is_configured  # noqa: E402

SECRETS_DRIVE_PATH = Path(r"G:\我的雲端硬碟\STOCKSCAN\_secrets\.env")


def parse_env_text(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines, tolerating PowerShell ``$env:KEY=VALUE`` style."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.lower().startswith("$env:"):
            key = key[len("$env:"):].strip()
        out[key] = value.strip().strip('"').strip("'")
    return out


def load_secrets_env() -> list[str]:
    """Load missing env vars from the unified secrets file(s); report sources."""
    loaded: list[str] = []
    candidates = []
    explicit = os.getenv("CCASS_SECRETS_ENV", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(SECRETS_DRIVE_PATH)
    candidates.append(Path.home() / ".stockscan" / ".env")
    for path in candidates:
        if not path.is_file():
            continue
        try:
            pairs = parse_env_text(path.read_text(encoding="utf-8-sig"))
        except OSError:
            continue
        fresh = [k for k, v in pairs.items() if v and not os.environ.get(k)]
        for key in fresh:
            os.environ[key] = pairs[key]
        if fresh:
            loaded.append(f"{path} ({len(fresh)} keys)")
        if os.getenv("TURSO_DATABASE_URL") and os.getenv("TURSO_AUTH_TOKEN"):
            break
    return loaded


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codes", nargs="*", default=[], help="Five-digit code5 values.")
    parser.add_argument("--codes-file", type=Path, help="Text/CSV file containing code5 values.")
    parser.add_argument("--panel", type=Path, help="Panel CSV; warms its unique code5 values.")
    parser.add_argument("--sleep", type=float, default=1.0, help="Idle seconds between dispatches.")
    parser.add_argument("--timeout", type=int, default=20, help="Per-stock Webb-site request budget.")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent stocks (mirror politeness).")
    parser.add_argument("--start-after", default="", help="Resume after this code5 (exclusive).")
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("data/warm_ccass_cache_checkpoint.json"),
        help="JSON checkpoint written after every attempted code5.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Optional maximum number of stocks.")
    parser.add_argument("--verify", type=int, default=0,
                        help="After warming, re-read N cache keys to prove Turso hits.")
    parser.add_argument("--skip-prefetch", action="store_true",
                        help="Skip the issue-id batch pre-pass (use existing stock_map only).")
    parser.add_argument("--prefetch-only", action="store_true",
                        help="Only run the issue-id pre-pass, then exit (lets the fetch "
                             "phase use different pacing without re-probing the mirror).")
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


def _disable_yahoo_side_fetch() -> None:
    """Warm never needs the Yahoo price side-fetch; on an empty snapshot DB it
    burns ~30s per stock.  Patch the router symbol before any bundle is built."""
    import utils.source_router as router
    from utils.fetcher import FetchResult

    def _no_yahoo(stock_code: str, period_days: int = 90) -> FetchResult:
        return FetchResult(
            name="Price History (yahoo disabled for warm)",
            url="disabled://yahoo",
            method="skipped",
            ok=False,
            error_type="DISABLED_FOR_WARM",
            error_message="Yahoo side-fetch disabled by warm_ccass_cache v2.",
        )

    router.fetch_yahoo_price_history = _no_yahoo


def prefetch_issue_ids(codes: list[str], workers: int, sleep_s: float,
                       timeout: int) -> dict[str, str]:
    """Resolve code5 -> issue_id once per stock into the persistent stock_map.

    Returns the map for codes that resolved.  Unresolved codes are reported so
    the caller can mark them FETCH_FAIL instead of re-trying per section.
    """
    from utils.fetcher import (
        extract_issue_id_from_html,
        fetch_with_requests,
        orgdata_url,
    )
    from utils.snapshot_db import load_stock_map, upsert_stock_map

    missing = []
    for code in codes:
        try:
            if not load_stock_map(code).get("issue_id"):
                missing.append(code)
        except Exception:
            missing.append(code)
    if not missing:
        print(json.dumps({"prefetch": "all-cached", "checked": len(codes)}), flush=True)
        return {}

    print(json.dumps({"prefetch": "start", "need_lookup": len(missing),
                      "workers": workers}), flush=True)
    resolved: dict[str, str] = {}
    lock = threading.Lock()
    done = 0
    error_types: dict[str, int] = {}

    def resolve(code: str) -> tuple[str, str, str]:
        result = fetch_with_requests("Company / orgdata", orgdata_url(code), timeout=timeout)
        if result.ok:
            issue_id, _method = extract_issue_id_from_html(result.html or "")
            return code, issue_id, ""
        return code, "", result.error_type or "FETCH_FAIL"

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = set()
        for index, code in enumerate(missing, start=1):
            futures.add(executor.submit(resolve, code))
            if index % max(1, workers) == 0 and index < len(missing):
                time.sleep(sleep_s)
        for future in as_completed(futures):
            code, issue_id, error = future.result()
            with lock:
                done += 1
                if error:
                    error_types[error] = error_types.get(error, 0) + 1
                if issue_id:
                    resolved[code] = issue_id
                    try:
                        upsert_stock_map(code, issue_id, name="")
                    except Exception:
                        pass
            if done % 25 == 0 or done == len(missing):
                print(json.dumps({"prefetch_progress": done, "resolved": len(resolved),
                                  "error_types": error_types}), flush=True)
    print(json.dumps({"prefetch": "done", "resolved": len(resolved),
                      "unresolved": len(missing) - len(resolved),
                      "error_types": error_types}), flush=True)
    return resolved


def warm_one(code: str, timeout: int) -> dict[str, object]:
    item_started = time.monotonic()
    try:
        payload = build_stock_payload(
            stock_code=code,
            timeout=timeout,
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
        seconds = round(time.monotonic() - item_started, 1)
        if not verified:
            return {"code": code, "status": "FETCH_FAIL", "seconds": seconds,
                    "errors": [str(e.get("error_code") or e) for e in errors[:3]]}
        cache_key = f"stock:{code}:light:v1"
        if not put_api_stock_cache(cache_key, payload):
            return {"code": code, "status": "TURSO_WRITE_FAIL", "seconds": seconds}
        return {"code": code, "status": "OK", "seconds": seconds}
    except Exception as exc:  # one stock must not discard completed cache entries
        return {"code": code, "status": "FETCH_FAIL", "error_type": type(exc).__name__,
                "seconds": round(time.monotonic() - item_started, 1)}


def verify_cache(count: int, codes: list[str]) -> list[str]:
    from utils.turso_db import get_api_stock_cache

    hits = []
    for code in codes[-count:] if count <= len(codes) else codes:
        payload = get_api_stock_cache(f"stock:{code}:light:v1", max_age_seconds=10 * 365 * 86400)
        if payload:
            hits.append(code)
    return hits


def main() -> int:
    args = parse_args()
    sources = load_secrets_env()
    if sources:
        print(json.dumps({"secrets_loaded_from": sources}), flush=True)
    codes = collect_codes(args)
    if not codes:
        raise SystemExit("No code5 supplied. Use --codes, --codes-file, or --panel.")
    if args.dry_run:
        print(json.dumps({"count": len(codes), "first": codes[:5], "last": codes[-5:]}))
        return 0
    if not turso_is_configured():
        raise SystemExit(
            "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are required; "
            "the local warm job refuses to fall back to Render."
        )

    if not args.skip_prefetch:
        resolved = prefetch_issue_ids(codes, max(1, args.workers),
                                      max(0.0, args.sleep) / 2, args.timeout)
        if args.prefetch_only:
            print(json.dumps({"prefetch_only": True, "resolved_this_run": len(resolved)}),
                  flush=True)
            return 0
        unresolved = [c for c in codes if c not in resolved]
        if unresolved and not resolved:
            # 全部查唔到 issue id：唔好盲目掃，直接標失敗留紀錄
            for code in unresolved:
                print(json.dumps({"code": code, "status": "ISSUE_UNRESOLVED"}), flush=True)
            return 2

    _disable_yahoo_side_fetch()

    started = time.monotonic()
    checkpoint = args.checkpoint if args.checkpoint.is_absolute() else REPO_ROOT / args.checkpoint
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_rows: list[dict[str, object]] = []
    if checkpoint.exists():
        try:
            previous = json.loads(checkpoint.read_text(encoding="utf-8"))
            checkpoint_rows = [
                row for row in (previous.get("completed") or []) if isinstance(row, dict)
            ]
        except (OSError, json.JSONDecodeError):
            checkpoint_rows = []
    ok_count = sum(row.get("status") == "OK" for row in checkpoint_rows)
    failed_count = sum(row.get("status") in {"FETCH_FAIL", "TURSO_WRITE_FAIL"}
                       for row in checkpoint_rows)

    total = len(codes)
    print(json.dumps({"warm_start": True, "codes": total, "workers": args.workers,
                      "sleep": args.sleep}), flush=True)
    done_in_run = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = set()
        for index, code in enumerate(codes, start=1):
            futures.add(executor.submit(warm_one, code, args.timeout))
            if index % max(1, args.workers) == 0 and index < total:
                time.sleep(max(0.0, args.sleep))
        for future in as_completed(futures):
            row = future.result()
            checkpoint_rows.append(row)
            done_in_run += 1
            if row["status"] == "OK":
                ok_count += 1
            elif row["status"] in {"FETCH_FAIL", "TURSO_WRITE_FAIL"}:
                failed_count += 1
            checkpoint.write_text(
                json.dumps(
                    {"updated_at": time.time(),
                     "last_code": max((str(r.get("code") or "") for r in checkpoint_rows),
                                      default=""),
                     "completed": checkpoint_rows},
                    ensure_ascii=False, indent=2),
                encoding="utf-8")
            if row["status"] != "OK" or done_in_run % 10 == 0 or done_in_run == total:
                print(json.dumps({"done": done_in_run, "of": total, **row}, ensure_ascii=False),
                      flush=True)

    seconds = [float(r["seconds"]) for r in checkpoint_rows
               if r.get("status") == "OK" and isinstance(r.get("seconds"), (int, float))]
    stats = {
        "total": total,
        "ok_cumulative": ok_count,
        "failed_cumulative": failed_count,
        "avg_seconds_per_ok": round(sum(seconds) / len(seconds), 2) if seconds else None,
        "max_seconds_per_ok": max(seconds) if seconds else None,
        "elapsed_s": round(time.monotonic() - started, 1),
    }
    if args.verify > 0:
        hits = verify_cache(args.verify, codes)
        stats["verify_requested"] = args.verify
        stats["verify_hits"] = len(hits)
    print(json.dumps(stats, ensure_ascii=False), flush=True)
    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
