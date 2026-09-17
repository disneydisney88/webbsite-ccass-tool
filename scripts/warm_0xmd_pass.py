#!/usr/bin/env python
"""P6b 0xmd emergency warm pass - RETIRED, kept as a findings record.

2026-09-17 verdict: webbsite.0xmd.com is a dead end for CCASS data.
  * Cloudflare clearance CAN be transplanted from a manually-solved Chrome
    session into python requests (cookie jar + exact UA, same IP); it worked
    at the HTTP level and the TTL proved short (~1-2h).
  * But the 0xmd CCASS section pages return 200 with a ~3KB JS shell -
    pandas finds no <table> (ValueError: no tables).  No data to parse.
  * Keep this file only as the documented playbook if 0xmd ever serves real
    tables again.  Do not schedule it.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

COOKIE_FILE = REPO / "data" / "_0xmd_cf_test.json"
MIRROR = "https://webbsite.0xmd.com"


def install_cookie_route() -> dict[str, int]:
    """Seed cookies into each Session's jar (requests normal cookie path -
    an explicit Cookie header gets 403'd by this CF zone; jar probe passes)."""
    import requests
    from requests.cookies import RequestsCookieJar

    d = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    ua = d["ua"]
    seed = RequestsCookieJar()
    for c in d["cookies"]:
        seed.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
    counter = {"blocked": 0, "ok": 0}
    orig = requests.Session.request

    def patched(self, method, url, **kw):
        if "webbsite.0xmd.com" in str(url):
            for c in seed:
                self.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
            headers = dict(kw.get("headers") or {})
            headers["User-Agent"] = ua
            kw["headers"] = headers
            resp = orig(self, method, url, **kw)
            if getattr(resp, "status_code", 0) == 403:
                counter["blocked"] += 1
            else:
                counter["ok"] += 1
            return resp
        return orig(self, method, url, **kw)

    requests.Session.request = patched
    import os
    os.environ["CCASS_MIRROR_BASE_URL"] = MIRROR
    return counter


def mapped_codes() -> list[str]:
    with sqlite3.connect(REPO / "data" / "ccass_snapshots.db") as con:
        rows = con.execute(
            "SELECT code FROM stock_map WHERE issue_id IS NOT NULL AND issue_id != ''"
        ).fetchall()
    return sorted(str(r[0]).zfill(5) for r in rows)


def main() -> int:
    from scripts.warm_ccass_cache import load_secrets_env  # noqa: E402

    load_secrets_env()

    done = {}
    try:
        d = json.loads((REPO / "data" / "warm_ccass_cache_checkpoint.json").read_text(encoding="utf-8"))
        for row in d.get("completed") or []:
            if isinstance(row, dict) and row.get("code"):
                done[str(row["code"])] = str(row.get("status") or "")
    except (OSError, json.JSONDecodeError):
        pass

    codes = [c for c in mapped_codes() if done.get(c) != "OK"]
    print(f"[0xmd] mapped-but-unwarmed: {len(codes)}", flush=True)
    if not codes:
        return 0

    counter = install_cookie_route()

    import scripts.warm_ccass_cache as warm

    warm._disable_yahoo_side_fetch()
    results = []
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = [ex.submit(warm.warm_one, c, 20) for c in codes]
        for i, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            results.append(row)
            if i % 20 == 0 or row["status"] != "OK":
                print(json.dumps({"progress": i, "of": len(codes),
                                  "blocked_ratio": counter["blocked"] / max(1, counter["ok"] + counter["blocked"]),
                                  **row}, ensure_ascii=False), flush=True)
    ok = sum(1 for r in results if r["status"] == "OK")
    print(json.dumps({"pass_done": True, "total": len(codes), "ok": ok,
                      "mirror_requests": counter}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
