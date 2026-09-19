#!/usr/bin/env python
r"""P6c 每日增量 warm：只磨「新上榜」＋「快過期」嘅股，唔再全 panel 大掃。

設計：panel 全 code 裡，(a) checkpoint 最新狀態唔係 OK 嘅（新上榜/之前失敗），
(b) Turso cache fetched_at 超過 --max-age 日的——兩類先 warm。每日一跑，
每餐鏡通常 <30 隻，遠低於 mirror 速率規則，唔會撞 403。
建議：本機排程或 STOCKSCAN Actions EOD 之後跑一次。

    python scripts/warm_incremental.py --panel "G:\我的雲端硬碟\STOCKSCAN\data\eod\radar_eod_panel_full.csv" --max-age-days 7
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)


def stale_codes(panel: Path, max_age_days: int) -> list[str]:
    from scripts.warm_ccass_cache import collect_codes, load_secrets_env, normalise_code
    load_secrets_env()
    parser = type("A", (), {"codes": [], "codes_file": None, "panel": panel,
                            "start_after": "", "limit": 0})
    all_codes = collect_codes(parser)
    from utils.turso_db import get_api_stock_cache
    out = []
    for code in all_codes:
        payload = get_api_stock_cache(f"stock:{code}:light:v1",
                                      max_age_seconds=max_age_days * 86400)
        if payload is None:
            out.append(code)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", type=Path, required=True)
    ap.add_argument("--max-age-days", type=int, default=7)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--sleep", type=float, default=1.5)
    args = ap.parse_args()

    codes = stale_codes(args.panel, args.max_age_days)
    log(f"incremental warm: {len(codes)} 隻需要更新（新上榜/過期/失敗）")
    if not codes:
        return 0
    cmd = [sys.executable, "scripts/warm_ccass_cache.py",
           "--codes", *codes, "--skip-prefetch",
           "--workers", str(args.workers), "--sleep", str(args.sleep)]
    subprocess.run(cmd, check=False, cwd=REPO)
    log("incremental warm done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
