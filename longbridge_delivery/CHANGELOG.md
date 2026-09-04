# Longbridge Round 3 Changelog

## Added

- Minimal MCP Streamable HTTP client with a strict read-only tool whitelist.
- OAuth 2.0 Device Authorization start/poll endpoints, dynamic client
  registration, encrypted token persistence, and automatic refresh.
- Agent Auth Code administration endpoint retained as an authentication
  fallback.
- `longbridge_holdings_daily` SQLite history keyed by stock, date, and CCASS ID.
- `source_preference=longbridge` and Longbridge fallback in `hybrid_light`.
- Date-aligned source comparison in `auto`; different dates are never compared.
- MCP `get_longbridge_broker_daily` for one participant's recent history.
- Streamlit Webb-site / Longbridge / Hybrid source selection.
- Longbridge authentication method, known expiry, remaining seconds, and
  refresh availability in `/health`.

## Schema Changes

- Holdings can include `data_date`, `ccass_id`, `participant_name`,
  `holding_shares`, `stake_pct_of_issued`, `stake_pct_of_ccass`, `source`, and
  `row_meaning`.
- Concentration can include both CCASS and issued-share denominators,
  `ccass_total_shares`, `ccass_total_pct_of_issued`, and `participant_count`.
- Compact stock responses can include `cross_check`.
- Metadata includes per-section date, row count, and source in `section_asof`.

## Compatibility

Existing Webb/local fields remain. Longbridge authentication or fetch failure
retains available Webb/local data and adds a warning rather than raising a
server error.
