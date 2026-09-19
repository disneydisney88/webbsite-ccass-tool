#!/usr/bin/env python
"""P6b chunked warm driver: small bursts + cooldowns to stay under the
webb-database.com Cloudflare rate rule.  Resolves issue ids inline (Yahoo
side-fetch is disabled inside warm_ccass_cache v2), tracks per-code latest
status in the shared checkpoint, and repeats until the panel is done.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.warm_ccass_cache import (  # noqa: E402
    collect_codes, load_secrets_env, parse_args as _unused  # noqa: F401
)

PANEL = Path(r"G:\我的雲端硬碟\STOCKSCAN\data\eod\radar_eod_panel_full.csv")
CHECKPOINT = REPO / "data" / "warm_ccass_cache_checkpoint.json"
RESUME_AFTER = "00351"
# 實測節律（2026-09-17/18）：burst ~60-100 隻後 mirror 硬 403，20-40 分鐘自動解封。
# 三個數全部可以環境變數覆寫，唔使改 code。
CHUNK = int(os.getenv("WARM_CHUNK", "100"))
CHUNK_COOL_S = int(os.getenv("WARM_CHUNK_COOL_S", "480"))   # 8 min cool between chunks
BAN_COOL_S = int(os.getenv("WARM_BAN_COOL_S", "900"))       # 15 min cool when blocked
MAX_CYCLES = int(os.getenv("WARM_MAX_CYCLES", "40"))


LOG_FILE = REPO / "data" / "warm_chunked_v2.log"


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def latest_status() -> dict[str, str]:
    try:
        d = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, str] = {}
    for row in d.get("completed") or []:
        if isinstance(row, dict) and row.get("code"):
            out[str(row["code"])] = str(row.get("status") or "")
    return out


def probe() -> bool:
    from utils.fetcher import fetch_with_requests, orgdata_url

    r = fetch_with_requests("probe", orgdata_url("01010"), timeout=12)
    return bool(r.ok)


def pending_codes(done: dict[str, str]) -> list[str]:
    parser = type("A", (), {"codes": [], "codes_file": None, "panel": PANEL,
                            "start_after": RESUME_AFTER, "limit": 0})
    all_codes = collect_codes(parser)
    return [c for c in all_codes if done.get(c) != "OK"]


def main() -> int:
    load_secrets_env()
    for cycle in range(1, MAX_CYCLES + 1):
        done = latest_status()
        pending = pending_codes(done)
        log(f"cycle {cycle}: pending={len(pending)} ok={sum(1 for c in done.values() if c == 'OK')}")
        if not pending:
            log("panel warm complete")
            return 0
        chunk = pending[:CHUNK]
        for attempt in range(6):
            if probe():
                break
            log(f"probe blocked, cool {BAN_COOL_S}s (attempt {attempt + 1})")
            time.sleep(BAN_COOL_S)
        cmd = [sys.executable, "scripts/warm_ccass_cache.py",
               "--codes", *chunk, "--skip-prefetch",
               "--workers", "3", "--sleep", "2"]
        log(f"chunk start: {chunk[0]}..{chunk[-1]} ({len(chunk)} codes)")
        subprocess.run(cmd, check=False, cwd=REPO)
        log("chunk done, cooldown")
        time.sleep(CHUNK_COOL_S)
    log("max cycles reached; pending may remain")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
