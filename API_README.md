# ChatGPT / Custom GPT API

This project includes a read-only JSON API for Custom GPT Actions or other tools.

## Local Test

```bash
python api.py
```

Open:

- `http://localhost:8000/health`
- `http://localhost:8000/openapi.json`
- `http://localhost:8000/api/stock?code=01592`
- `http://localhost:8000/api/stock?code=01592&timeout=30&holdings_limit=15&changes_limit=20&big_changes_limit=10&concentration_limit=15`

## Optional API Token

If you want to protect the API, set this environment variable on the server:

```text
API_TOKEN=<your-random-token>
```

If `API_TOKEN` is not set, the read-only stock endpoint is public.

URL-only clients should pass the token as a query parameter:

```text
GET /api/stock?code=01592&key=<your-random-token>
GET /api/stock?code=01592&api_token=<your-random-token>
```

Bearer and `X-API-Key` are still accepted for clients that support custom headers:

```text
Authorization: Bearer <your-random-token>
X-API-Key: <your-random-token>
```

## Custom GPT Action

ChatGPT cannot call your local `localhost` directly. Deploy this API to Render or another HTTPS host.

Then import this URL into a Custom GPT Action:

```text
https://your-domain/openapi.json
```

The main action endpoint is:

```text
GET /api/stock?code=01592
```

It returns one pure JSON response from a single HTTP GET. The response includes:

```json
{
  "metadata": {
    "code": "01592",
    "name": "...",
    "issue_id": "...",
    "holdings_date": "...",
    "changes_date": "..."
  },
  "holdings_summary": {},
  "holdings": [],
  "changes": [],
  "big_changes": [],
  "concentration": {
    "top5_pct": "...",
    "top10_pct": "...",
    "latest_date": "...",
    "records": []
  },
  "fetch_summary": [],
  "data_quality_warnings": []
}
```

## Additional endpoints

Same auth as `/api/stock` (`?key=`, `?api_token=`, Bearer or `X-API-Key`).

### Corporate events — `GET /api/stock/events?code=03321`

Webb-site capital actions and distributions: dividends, splits/consolidations,
bonus issues, rights, etc. Each event carries `announced`, `year_end`, `type`,
`amount`, `new_old` (e.g. `1:10` for a 10-into-1 consolidation), `ex_date`,
`distribution`, `notes`, plus `event_id` and `event_details_url`.

Optional: `limit` (default 30, max 200).

### Directors & officers — `GET /api/stock/officers?code=03321`

Webb-site board and management: `name`, `person_id`, `person_url`, `sex`, `age`,
`position_code` (e.g. `ED`, `INED`, `NED`, `CoSec`, `CFO`), `position` (full
title), `from_date`, `until_date`, `is_current` and `table_group` (e.g.
`Main board` vs `Manager/adviser/other`).

Optional: `snapshot_date` (`YYYY-MM-DD`). Note: Webb-site stopped updating
officer data after 2025-03-31; that source notice is echoed in
`data_quality_warnings`.

The same response also carries `managers_f10`: current management sourced from
同花順 F10 (`basic.10jqka.com.cn/HK<code>/manager.html`) with `name`,
`positions`, `tenure_from`/`tenure_to`, `is_current`, `sex`, `age`,
`education`, `salary` and the full `biography` — this covers appointments after
the Webb-site freeze. `managers_f10_source` documents the provider and URL.
Fields the source omits are null.

MCP tools: `get_stock_events`, `get_stock_officers` (in addition to
`get_ccass_stock_data`, `get_webbsite_price_history`, `get_hkex_announcements`).

## CHANGELOG

Breaking or behavioural changes are recorded here. Existing field names are kept
for at least one version when new fields are added, because downstream analysis
flows depend on the current schema.

### Unreleased

- **Explicit response schema:** `holdings`, `changes`, `big_changes` and
  `concentration.records` items are now typed models in `/openapi.json`, so the
  normalized fields (`participant_id`, `change_shares`, `change_pct`, `category`
  and the concentration dual-basis fields) are discoverable by GPT/Claude
  Actions instead of relying on `additionalProperties`. Arbitrary source columns
  (e.g. `Stake %`, `CCASS ID`) still pass through unchanged. Re-import
  `/openapi.json` to pick up the fuller schema.
- **Participant categories:** every holdings, changes and big-changes row now
  carries a `category` (`retail` / `bank` / `boutique` / `intl_broker` /
  `unknown`) to make collecting vs distributing flows readable at a glance. The
  mapping lives in `config/participant_categories.json` — add a CCASS ID under
  `by_ccass_id` (most reliable) or a distinctive name fragment under
  `by_name_keyword` to classify a new broker; no code change needed.
- **Settlement metadata:** `metadata` now includes `data_as_of_trading_date`
  and `settlement_note` (CCASS is T+2), so consumers do not have to reason about
  the settlement lag themselves.
- **Concentration dual-basis:** the `concentration` block and every record now
  carry both `top5_pct_of_ccass` / `top10_pct_of_ccass` (the source page's basis,
  % of shares in CCASS) and `top5_pct_of_issued` / `top10_pct_of_issued` (% of
  total issued shares), plus `issued_shares` and `issued_shares_as_of`. When a
  % of issued exceeds 100% (stale issued-share base after a placement/
  consolidation) the record and the summary set `issued_shares_may_be_stale` and
  a warning is added. The legacy `top5_pct` / `top10_pct` (of issued) are
  unchanged.
- **New data — Corporate events** (`GET /api/stock/events`, MCP `get_stock_events`):
  Webb-site dividends, splits/consolidations, bonus, rights and other capital
  actions, keyed by issue id.
- **New data — Directors & officers** (`GET /api/stock/officers`, MCP
  `get_stock_officers`): Webb-site board and management with positions and tenure,
  keyed by the Webb-site organisation id. Includes the source's post-2025-03-31
  update-freeze notice as a data-quality warning.
- **Reliability:** CCASS sections (Holdings, Changes, Big Changes, Concentration,
  Price History) are now fetched **concurrently** instead of serially. Previously
  Price History — always fetched last — was frequently starved of the shared
  timeout budget and returned `Timeout budget exhausted before this section was
  fetched`. Concurrency removes that starvation and lowers warm-request latency.
  Tune worker count with the `FETCH_MAX_WORKERS` env var (default = number of
  sections).
- **Lower default limits** (callers can still request more, up to the same maxima):
  `holdings_limit` 20 → **15**, `changes_limit` 30 → **20**, `big_changes_limit`
  20 → **10**, `concentration_limit` 30 → **15**.
- **Big Changes enrichment (additive):** each big-change row now also carries
  `participant_id` (joined from the Holdings table; `null` when the name cannot be
  matched — never fabricated), `participant_name`, `change_shares` (numeric, `null`
  when the source omits a share-count column) and `change_pct`. The original
  `Date`, `Participant`, `Change %` and `Change in shares` keys are unchanged.
