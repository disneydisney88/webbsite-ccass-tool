# CCASS Tool Round 3 Longbridge Manifest

Generated: 2026-09-05

## Authentication Route

The selected primary route is OAuth 2.0 Device Authorization against Longbridge OpenAPI. Production health reported `longbridge=authenticated`, OAuth device-flow authentication, and refresh available. The latest production SHA observed during verification was `8cd246b`; GitHub HEAD is `e730f9a`, with the `2e21446` code deployment and `e730f9a` manifest-only deployment still pending Render confirmation.

## Percentage Denominator

The live 06182 `broker_holding_detail` response contained 103 source rows and 103 normalized participant rows. B01438 had `shares.value=540,928,000` and `ratio.value=0.6761`, so the native ratio is 67.61% and implies 800,070,995 shares (approximately 800M issued shares). The normalized participant sum was 799,328,000 shares, giving independently recomputed `stake_pct_of_ccass=67.672845%`. The export does not copy `ratio.value` into the CCASS percentage field.

The one source row with missing point-in-time shares is `B01161` (UBS Securities Hong Kong Limited). It is retained as `holding_shares=0` with warning `holding_missing_in_source`; its `shares.chg_1=-432,000` is retained in Changes.

## Credential Storage

The encrypted token blob is exported as `longbridge_token.enc` for upload as a Render Secret File. Set `LONGBRIDGE_TOKEN_FILE=/etc/secrets/longbridge_token.enc` and retain `LONGBRIDGE_TOKEN_KEY`; when configured, the file is authoritative and the loader does not silently fall back to the repository SQLite backup. No plaintext token is committed.

## Acceptance Status

1. Device start/poll, refresh, and health expiry fields: **PASS (live health; fixture for expiry paths)**.
2. 06182 live Holdings >=100 rows: **PASS** (103 raw / 103 normalized; B01438 540,928,000).
3. Dual denominator fields: **PASS** (67.61% issued-native; 67.672845% recomputed CCASS).
4. Derived concentration: **PASS** (recomputed from all 103 normalized holdings: Top5 issued 89.671573%, Top5 CCASS 89.754925%, Top10 issued 93.518732%, Top10 CCASS 93.605660%, CCASS total 799,328,000, participants 103).
5. B01438 daily live history: **PASS** (40 rows; 2026-08-31 change -90,000,000).
6. Encrypted/corrupt-token handling: **PASS (fixture evidence)**; live corrupt-token probe not run.
7. Four stock-symbol conversions: **PASS (fixture evidence)**; live 06182 conversion used `6182.HK`.
8. ISO date normalization: **PASS**; live `2026.09.04` became `2026-09-04`, and daily dates were normalized likewise.
9. Read-only whitelist: **PASS (live)**; `tools/list` returned 162 total tools and all 6 required read-only tools were present.
10. `caiji` snapshot run without rate limiting: **PASS (live)**; 28/28 succeeded, 0 failed, 0 observed 429 responses, all dated 2026-09-04. The exported production SQLite contained 3,735 Longbridge holding rows for that date across the 28 stocks; the endpoint does not expose a before/after delta.

## Component Status

- P1 SQLite accumulation: **DONE**. `longbridge_holdings_daily` is upserted by stock/date/participant during stock and broker-daily fetches.
- P2 cross-check: **PARTIAL**. Longbridge holdings and denominator evidence are live; same-date Webb comparison was not captured, so no numeric cross-check is claimed.
- `get_longbridge_broker_daily`: **DONE**. The MCP tool and protected raw route are available; live 06182/B01438 returned 40 rows.
- P3 Streamlit: **PARTIAL**. A Plotly Top-10 plus grey Others stacked-area chart with Shares/% issued/% CCASS selectors and daily-change hover data is implemented. Local Streamlit started successfully, but the local environment had no Longbridge token configured, so the 06182 live chart screenshot proving the -90M/+90M transfer remains pending.

## Offline Verification

**215 passed, 5 warnings, 10 subtests passed in 19.24s.**

## File Hashes

| File | SHA-256 |
|---|---|
| `06182_longbridge_20260904.json` | `f9068afb09213c116cdbf1671d132d75f91adc041ea06827a4cf442948a6291a` |
| `06182_cross_check_20260904.json` | `eb78dc71ad81a2d681dbcf98453c2db304ccb504e61a9da493b3bb1ca5e3150f` |
| `06182_B01438_daily.json` | `c2302a1a9e9336ba361c6a97355b4bd6b0c1fe758bd0c8c0542561b5aaadc48` |
