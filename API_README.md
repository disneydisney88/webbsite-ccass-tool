# Webb-site CCASS Research API 1.14.0

Read-only FastAPI and MCP service for Hong Kong CCASS research. Existing response field names are preserved; date semantics and source diagnostics are additive.

Production base URL:

```text
https://webbsite-ccass-api.onrender.com
```

Useful URLs:

- `/health`
- `/health?upstreams=true`
- `/openapi.json`
- `/api/stock?code=03301`
- `/mcp`

## Authentication

If `API_TOKEN` is set, use one of:

```text
GET /api/stock?code=03301&key=<token>
GET /api/stock?code=03301&api_token=<token>
Authorization: Bearer <token>
X-API-Key: <token>
```

Tokens must never be committed. GitHub Actions uses repository secret `CCASS_API_TOKEN`, whose value is the same as Render `API_TOKEN`.

## GET /api/stock

Operation ID: `getCCASSStockData`.

| Parameter | Default | Description |
| --- | ---: | --- |
| `code` | required | Five-digit HK stock code; `stock_code` remains a compatible alias |
| `timeout` | 30 | Overall budget, 10 to 35 seconds |
| `holdings_limit` | 15 | 1 to 100 rows |
| `changes_limit` | 20 | 1 to 100 rows |
| `big_changes_limit` | 10 | 1 to 100 rows |
| `concentration_limit` | 15 | 1 to 100 rows |
| `changes_from` | empty | Changes range start, `YYYY-MM-DD` |
| `changes_to` | empty | Changes range end, `YYYY-MM-DD` |
| `big_changes_from` | empty | Big Changes range start, `YYYY-MM-DD` |
| `big_changes_to` | empty | Big Changes range end, `YYYY-MM-DD` |
| `date_input_basis` | `trade` | `trade` or `settlement` |
| `format` | `json` | `json`, `markdown`, or `md` |

Date ranges must start and end on XHKG trading sessions. The default maximum is 20 sessions, configurable with `CCASS_MAX_DATE_RANGE_SESSIONS`. A larger range returns HTTP 413 with `TOO_LARGE`.

Example using trade dates:

```text
GET /api/stock?code=03301&changes_from=2026-08-07&changes_to=2026-08-10&date_input_basis=trade
```

The router requests the corresponding Changes settlement pages:

| Requested trade date | Queried settlement date |
| --- | --- |
| 2026-08-07 | 2026-08-11 |
| 2026-08-10 | 2026-08-12 |

No calendar-day `+2` guess is used.

## Date Contract

| Section | `date_basis` | Row `ccass_date` |
| --- | --- | --- |
| Holdings | `settlement` | Page holding date |
| Changes | `trade` | Page's explicit Trading date |
| Big Changes | `settlement` | Source Date column |
| Concentration | `settlement` | Source Date column |
| Price History | `trade` | Trade date |

Every normalized record may include:

```json
{
  "ccass_date": "2026-08-12",
  "implied_trade_date": "2026-08-10",
  "implied_settlement_date": "2026-08-12",
  "date_basis": "settlement"
}
```

`data_as_of_trading_date` is the latest valid implied trade date among successfully dated CCASS sections. It is never silently replaced by a settlement date. If no dated section parsed, it contains a reason such as `not available: no dated CCASS section parsed`.

Full evidence and holiday-calendar details: [docs/DATE_SEMANTICS.md](docs/DATE_SEMANTICS.md).

## Response Example

Values below are illustrative; clients must inspect `fetch_summary` and warnings on every request.

```json
{
  "ok": true,
  "data_as_of": "2026-08-10",
  "source": "hybrid_local_db_webb",
  "metadata": {
    "code": "03301",
    "name": "Ronshine China Holdings Limited",
    "issue_id": "18546",
    "holdings_date": "2026-08-12",
    "holdings_implied_trade_date": "2026-08-10",
    "changes_date": "2026-08-10",
    "changes_implied_trade_date": "2026-08-10",
    "big_changes_date": "2026-08-12",
    "big_changes_implied_trade_date": "2026-08-10",
    "concentration_date": "2026-08-12",
    "concentration_implied_trade_date": "2026-08-10",
    "data_as_of_trading_date": "2026-08-10",
    "date_basis_by_section": {
      "holdings": "settlement",
      "changes": "trade",
      "big_changes": "settlement",
      "concentration": "settlement"
    },
    "date_input_basis": "trade",
    "changes_requested_from": "",
    "changes_requested_to": "",
    "changes_queried_settlement_from": "",
    "changes_queried_settlement_to": "",
    "settlement_note": "CCASS uses T+2 settlement...",
    "source": "hybrid_local_db_webb",
    "mirror_status": "direct_pages_only",
    "mirror_base_url": "https://webb-database.com",
    "history_depth_days": 2,
    "db_restored_from_backup": true,
    "price_source": "mirror"
  },
  "holdings_summary": {
    "total_in_ccass": "1278778238",
    "total_in_ccass_pct": "75.96%",
    "securities_not_in_ccass": "404652679",
    "largest_participant": "THE HONGKONG AND SHANGHAI BANKING",
    "holdings_total_count": 36,
    "holdings_returned_count": 15,
    "changes_total_count": 20,
    "changes_returned_count": 20,
    "big_changes_total_count": 329,
    "big_changes_returned_count": 10,
    "concentration_total_count": 1980,
    "concentration_returned_count": 15,
    "truncated": true
  },
  "holdings": [],
  "changes": [],
  "big_changes": [],
  "concentration": {
    "top5_pct": "71.55%",
    "top10_pct": "83.18%",
    "latest_date": "2026-08-12",
    "records": []
  },
  "price_history": [],
  "fetch_summary": [],
  "data_quality_warnings": [],
  "errors": []
}
```

If Holdings fails but Concentration succeeds, Top 5 and Top 10 remain populated from Concentration. Holdings-only values such as `largest_participant` and `total_in_ccass_pct` contain an explicit unavailable reason rather than an empty string.

## Fetch Diagnostics

Each `fetch_summary`／`fetch_log` row can include:

- section and URL
- final URL
- fetch status and HTTP status
- content type
- tables found and selected table
- method and fallback method
- error type and error message
- first 2,000 normalized response characters as `body_head`／`response_snippet`

One failed section does not stop successful sections. Empty local data is not proof that upstream CCASS data do not exist.

## Source Router

```text
CCASS_SOURCE_MODE=auto|mirror|sdw|local_db
CCASS_MIRROR_BASE_URL=https://webb-database.com
```

- `auto` is the REST and MCP default.
- Cache keys include stock code, source preference and mirror base URL.
- `mirror` preserves the original fetcher/parser.
- `sdw`／`local_db` reads accumulated SQLite snapshots.
- Cloudflare/Turnstile receives no bypass. A 403 fails loudly and available local data are returned where possible.

MCP `get_ccass_stock_data` and REST `/api/stock` now both call `build_stock_payload(..., source_preference="auto")`.

## Structured Errors

```json
{
  "error_code": "SOURCE_TIMEOUT",
  "message": "Holdings: request timed out",
  "retry_recommended": true
}
```

| Error code | Meaning | Retry recommended |
| --- | --- | --- |
| `COLD_START` | Hosting wake-up or total budget exhausted | yes |
| `SOURCE_TIMEOUT` | Upstream timeout | yes |
| `SOURCE_FETCH_FAILED` | DNS/network/5xx failure | yes, once |
| `MIRROR_BLOCKED` | 403 or human verification | no |
| `SOURCE_CHALLENGE` | JavaScript/human verification challenge | no |
| `SOURCE_CHANGED` | No matching table or source layout changed | no |
| `PARSE_ERROR` | Response fetched but parser could not normalize it | no |
| `LOCAL_SNAPSHOT_EMPTY` | Local DB has no snapshot for this code | no; this does not mean no CCASS data exist |
| `ISSUE_LOOKUP_FAILED` | Webb issue ID resolution failed | normally no high-frequency retry |
| `INVALID_DATE_RANGE` | Invalid/non-session date bound | no |
| `TOO_LARGE` | More sessions/rows than allowed | no; reduce range |
| `AUTH_FAILED` | Token missing or incorrect | no |

## Other REST Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /api/screen?codes=...` | Compact screening of up to 20 stocks |
| `GET /api/participant?id=...&codes=...` | Participant footprint across supplied codes |
| `GET /api/stock/price?code=...` | Webb/Yahoo price history |
| `GET /api/stock/announcements?code=...` | HKEX announcements |
| `GET /api/stock/events?code=...` | Webb corporate events |
| `GET /api/stock/officers?code=...` | Webb officers plus current F10 managers |
| `GET /api/stock/capital?code=...` | F10 share capital changes and buybacks |
| `GET /api/stock/diff?code=...&date_a=...&date_b=...` | Local snapshot diff |
| `GET /announcement/pdf?url=...` | Text extraction for allowlisted HKEX PDFs |
| `GET /api/snapshot_all?group=...` | Daily watchlist snapshot trigger |
| `GET /api/snapshots/export` | Download SQLite backup |

The four unaffected paths remain separate: HKEX announcements, HKEX PDF extraction, Yahoo price fallback and F10 capital/management.

## MCP Tools

Main tools include:

- `get_ccass_stock_data`
- `screen_stocks`
- `search_participant_holdings`
- `get_ccass_diff`
- `get_webbsite_price_history`
- `get_hkex_announcements`
- `fetch_announcement_pdf`
- `get_stock_events`
- `get_stock_officers`
- `get_stock_capital`

`get_ccass_stock_data` accepts the same date range arguments as REST: `changes_from`, `changes_to`, `big_changes_from`, `big_changes_to`, `date_input_basis`.

## Snapshot Persistence

Default DB: `data/ccass_snapshots.db`. Render Free filesystem is ephemeral, so `.github/workflows/daily_snapshot.yml` exports the DB into the repository backup path and startup restores it when the working DB is missing.

```text
GET /api/snapshot_all?group=caiji&key=<token>
GET /api/snapshot_all?group=lshape&key=<token>
GET /api/snapshots/export?key=<token>
```

Metadata fields `history_depth_days` and `db_restored_from_backup` show the available local history and restore state. This backup mechanism reduces loss but is not a true persistent database. Consider persistent disk, Turso, Neon or Supabase before the DB exceeds about 50 MB.

## Price Fallback

Mirror `hpu.asp` is preferred because it includes actual turnover. Yahoo fallback adds `price_source=yahoo`, `turnover_est` and `vwap_est`; warnings state that these are estimates. Prices are rounded to three decimals and estimated turnover to two decimals at the output layer.

## Custom GPT Setup

Import:

```text
https://webbsite-ccass-api.onrender.com/openapi.json
```

Configure API-key authentication with the same server token. Re-import OpenAPI after deployment so the GPT sees row-level date fields and the new range parameters.

## Changelog

### 1.14.0

- Explicit trade/settlement contract and XHKG T+2 conversion.
- Row fields `ccass_date`, `implied_trade_date`, `implied_settlement_date`, `date_basis`.
- Metadata `data_as_of_trading_date`, section basis map and query/settlement ranges.
- Date range requests for Changes and Big Changes.
- Shared REST/MCP/Streamlit auto source router and source-aware caching.
- Extended raw diagnostics and partial-success summary behavior.

