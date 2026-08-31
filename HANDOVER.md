# CCASS Tool Engineering Handover

Date: 2026-08-26
Repository: `https://github.com/disneydisney88/webbsite-ccass-tool.git`
Working copy: `G:\我的雲端硬碟\Codex\2026-05-25\full-stack-python-webb-site-ccass\webbsite-ccass-tool-github`
Branch: `main`

## Important Status

This document is written in the GitHub-linked repository. The parser repair and the 08245 golden-case follow-up are committed and pushed to `main`; deployment is still pending live verification. Local test success must not be treated as Streamlit Cloud or Render acceptance.

## Current Git State

At handover time, the GitHub-linked repo is at:

`d79d11b Harden CCASS parsing and partial exports` (base repair commit; see the latest commit for the 08245 follow-up).

Existing user work in the working tree must be preserved:

- modified: `api.py`
- modified: `test_api_auth.py`
- modified: `test_sdw_snapshot.py`
- modified: `utils/errors.py`
- modified: `utils/snapshot_db.py`
- modified: `utils/source_router.py`
- untracked: `stock_01592_ccass.json`

`stock_01592_ccass.json` is a user file. Do not edit, delete, reset, or include it in a repair commit.

## Repair Scope Completed

The repository now contains the implementation for the 2026-08-26 repair specification and the 08245 golden-case follow-up:

- Big Changes dates are normalized to ISO `YYYY-MM-DD`, with forward-fill and date sanity checks.
- Big Changes keeps percentage changes distinct from absolute share changes. Absolute shares are marked as source-reported or estimated; estimates are never presented as source-reported.
- Holdings and Changes are parsed independently from Company/orgdata and retain cleaned participant IDs, canonical names, row dates, and numeric aliases.
- Participant hygiene includes canonical CCASS IDs/names, including HSBC `C00019`.
- Concentration validates percentage ranges, detects duplicate-date conflicts, marks suspicious denominator values, and checks Top 5 identity against Holdings.
- Each section has `section_asof` metadata: latest date, row count, status, date basis, and trading-session lag.
- Exporters preserve UTF-8-SIG CSV output, ISO dates, raw previews, section metadata, and existing legacy columns.
- API metadata and Big Changes records expose the new date/denominator/estimate fields without removing old field names.
- Mirror fetching remains intact: requests is tried first and Playwright remains the fallback. No Cloudflare bypass was added.
- Critical section failures are classified as `partial`; the Streamlit combined export is named `*.PARTIAL.csv` when Holdings, Concentration, or Price History is unavailable.
- Holdings exports include `shares_held`, `issued_shares_at_date`, `ccass_total_at_date`, and participant `last_change_date` when supplied by the source.
- Big Changes preserves explicit CCASS IDs and the golden 08245 regression fixture is offline-only.

Still open from the 08245 brief: independent Non-CCASS time-series output, structured warning records, corporate-action adjacency flags, and deployment-environment Playwright verification.

## Prompt 2 Follow-up: Challenge Diagnostics and Probe Decision

- Failed Holdings/Changes `FetchResult` objects are retained in the hybrid bundle, including status, URL, response details, error type, and error message.
- A JavaScript cookie/reload response is classified as `JS_CHALLENGE`; this remains distinct from an empty source or parser miss.
- `CCASS_DEBUG_DUMP` controls optional raw Holdings/Changes diagnostic files and is off by default. The `debug/` directory is ignored by Git.
- `mirror_probe()` remains defined but is not connected to the `auto` route. The current decision is **do not connect it in this task**. Connecting it later would add a daily upstream request, probe-cache semantics, and a decision about whether a failed probe triggers SDW fallback or a browser fetch. This needs a separate reviewed change because it affects frequency and source selection.
- README was corrected so it no longer promises a daily auto probe that does not currently run.

## Verification Evidence

Completed locally in the GitHub-linked repo:

- `python -m py_compile app.py api.py utils/parser.py utils/exporters.py utils/snapshot_db.py`
- Full test suite: `150 tests OK` after adding the 08245 regression; the previous `148` run did not include that test.
- The separate copy's `164` count included 13 tests from four files not present in this repo at that time (`test_08191_regressions.py`, `test_date_ranges.py`, `test_report_partial.py`, `test_source_routing.py`) plus three count differences; those files were not silently claimed as ported.
- 08245 golden regression: `2 tests OK`, using an offline fixture and no live fetch.
- 03301 regression tests: `6 tests OK`
- 08191 regression tests: `42 tests OK` in the relevant run
- Generated CSV has UTF-8-SIG BOM (`EF BB BF`) and generated Excel opens with separate section sheets.
- Local Streamlit health check on port 8502 returned `ok`.

A one-time live check for 03301 / issue `18546` in the separate copy reached `webb-database.com` successfully:

- Company: HTTP 200
- Holdings: Playwright fallback, HTTP 200, 3 tables
- Changes: Playwright fallback, HTTP 200, 3 tables
- Big Changes: HTTP 200, 2 tables
- Concentration: HTTP 200, 2 tables
- Holdings latest date: `2026-08-25`
- Changes trading date: `2026-08-21`
- HSBC row: `C00019`, canonical full name, holding `624098789`, stake `37.07`

These live dates are evidence from the local test run only; they are not a claim about the current production deployment.

## Files To Review / Port

Main implementation files in the separate copy:

- `app.py`
- `api.py`
- `utils/fetcher.py`
- `utils/parser.py`
- `utils/report.py`
- `utils/exporters.py`
- `utils/snapshot_db.py`
- `utils/source_router.py`
- `utils/date_semantics.py`
- `API_README.md`
- `README.md`
- `test_03301_regressions.py`
- `test_08245_regressions.py`

The target repository has its own later source-router, snapshot, auth, and error changes. Port the repair by comparing files and applying compatible hunks; do not copy the separate tree over the target repository wholesale.

## Required Handover Procedure

1. Run `git status --short` in the GitHub-linked repo and preserve all existing user changes.
2. Compare the separate repair copy with this repo, especially `api.py`, `utils/parser.py`, `utils/exporters.py`, `utils/source_router.py`, and `utils/snapshot_db.py`.
3. Merge only compatible repair changes. Keep all existing mirror logic and all existing JSON field names.
4. Add or port fixture-based tests for 03301 and 08191. Tests must not live-fetch Webb-site or SDW.
5. Run `python -m unittest` and compile checks from this repository.
6. Verify 03301 and 08191 with both zero-padded and non-padded input forms where supported.
7. Inspect generated CSV for UTF-8-SIG BOM, ISO dates, section labels, source URLs, fetch time, warnings, and no mixed-column shifts.
8. Stage only repair files and tests. Do not stage `stock_01592_ccass.json` or unrelated user modifications.
9. Commit with a clear message such as `Repair CCASS section dates and denominator validation`.
10. Push `main`, then verify Render, Streamlit, API, and MCP separately. In particular, run 08245 on Streamlit Cloud and confirm whether Holdings/Changes use Playwright or a local fallback; `partial` must remain visible if browser dependencies are unavailable.

## Acceptance Checks

For 03301 / issue `27882` or the current resolved issue ID:

- Holdings and Changes must be independently fetched/parsed, or clearly report `SOURCE_EMPTY` versus `PARSE_MISS`.
- No Company/orgdata row such as `HK Main`, `Code`, or `Listed` may appear as a Changes participant.
- Big Changes dates must be ISO and must not silently become one constant date.
- Concentration must have at most one retained row per data date; abnormal percentages must be warned/withheld.
- Metadata must expose `section_asof` and date basis by section.
- A stale section must identify its lag in XHKG trading sessions.

For 08191:

- Holdings/Changes parser failures must remain distinguishable from an empty source.
- The post-capital-change concentration date must be marked suspect or excluded when denominator validation requires it.
- Price and Big Changes rows must retain their individual source dates.
- Constance Capital and Futu rows should be checked against the supplied 2026-07-23 to 2026-08-14 fixture.

## Safety / Source Policy

- No mirror code was intentionally deleted in the repair copy.
- No Cloudflare, CAPTCHA, stealth-browser, or anti-bot bypass is permitted.
- Public pages only; no login or paywall bypass.
- Keep request frequency polite and preserve raw HTML/table previews for diagnosis.
- Never print or commit API tokens. If logging configuration, show only masked values.

## Deployment State

The repair described above is local-only until merged into the GitHub-linked repo. The next engineer owns the final diff review, commit/push, and live acceptance. A successful local test does not by itself update Streamlit Cloud, Render, or the GPT Action/MCP deployment.
