from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.upstream_probe import probe_upstreams


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Probe CCASS upstream sources and print HTTP diagnostics.")
    parser.add_argument("--code", default="01592", help="HK stock code for F10/HKEX checks.")
    parser.add_argument("--issue-id", default="26603", help="Webb-site issue id for mirror checks.")
    parser.add_argument("--timeout", type=int, default=8, help="Per-request timeout in seconds.")
    args = parser.parse_args()

    print(json.dumps(probe_upstreams(stock_code=args.code, issue_id=args.issue_id, timeout=args.timeout), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
