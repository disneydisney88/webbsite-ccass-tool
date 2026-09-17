#!/bin/bash
# P6b babysitter: probe webb-database.com until the 403 ban lifts, then resume
# the panel warm at mirror-friendly pacing. Logs to data/warm_babysit.log.
cd "$(dirname "$0")/.." || exit 1
PANEL='G:\我的雲端硬碟\STOCKSCAN\data\eod\radar_eod_panel_full.csv'

probe_status() {
  python - <<'PYEOF' 2>/dev/null | tail -1
from utils.fetcher import fetch_with_requests, orgdata_url
r = fetch_with_requests('probe', orgdata_url('01010'), timeout=12)
print('OK' if r.ok else 'BLOCKED')
PYEOF
}

while true; do
  s=$(probe_status)
  echo "$(date -u '+%Y-%m-%d %H:%M:%S') probe=$s"
  [ "$s" = "OK" ] && break
  sleep 300
done

echo "unblocked -> gentle prefetch (2 workers, sleep 2)"
python scripts/warm_ccass_cache.py --panel "$PANEL" --start-after 00351 \
  --prefetch-only --workers 2 --sleep 2

echo "prefetch done -> warm phase (4 workers, sleep 1.5)"
python scripts/warm_ccass_cache.py --panel "$PANEL" --start-after 00351 \
  --skip-prefetch --workers 4 --sleep 1.5

echo "warm done -> retry non-OK codes once"
RETRY_CODES=$(python - <<'PYEOF'
import json
from pathlib import Path
d = json.loads(Path('data/warm_ccass_cache_checkpoint.json').read_text(encoding='utf-8'))
seen = {}
for r in d.get('completed') or []:
    seen[r['code']] = r['status']
bad = sorted(c for c, s in seen.items() if s != 'OK')
print(' '.join(bad))
PYEOF
)
if [ -n "$RETRY_CODES" ]; then
  echo "retrying: $RETRY_CODES"
  python scripts/warm_ccass_cache.py --codes $RETRY_CODES --skip-prefetch \
    --workers 2 --sleep 2
fi
echo "babysitter finished"
