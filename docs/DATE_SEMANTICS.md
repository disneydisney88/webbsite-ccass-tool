# CCASS Date Semantics

## AI-ready summary

> Holdings, Big Changes and Concentration use CCASS holding/settlement dates (T+2). Changes participant rows use the source page's explicit Trading date, while the Changes page heading and `d=` query use settlement dates. Convert between them with two XHKG trading sessions, never two calendar days.

## Section-by-section basis

| Section | Source date shown by the page | `date_basis` | Normalized row fields |
| --- | --- | --- | --- |
| Holdings | Heading: `CCASS holdings on YYYY-MM-DD` | `settlement` | `ccass_date` is the heading date; `implied_trade_date` is two XHKG sessions earlier |
| Changes | Heading: `CCASS holding changes from ... to ...`, plus an explicit `Trading date` under `Trades that settled in this date range` | `trade` | Participant rows use the explicit Trading date as `ccass_date` and `implied_trade_date`; `implied_settlement_date` is two XHKG sessions later. The queried holding range is returned separately. |
| Big Changes | Column: `Date Y-M-D`; page says these are daily movements in CCASS holders | `settlement` | `ccass_date` is the source Date; `implied_trade_date` is two XHKG sessions earlier |
| Concentration | Column: `Date` in CCASS concentration history | `settlement` | `ccass_date` is the source Date; `implied_trade_date` is two XHKG sessions earlier |

The bases are deliberately not forced to one global value. In particular,
Changes exposes both a settlement range and a separate Trading date. The API
uses the Trading date for its participant rows because that is the field the
source explicitly associates with the trades that settled in the range.

Source examples:

- [Holdings for issue 18546](https://webb-database.com/ccass/choldings.asp?i=18546)
- [Changes for issue 18546](https://webb-database.com/ccass/chldchg.asp?i=18546)
- [Big Changes for issue 18546](https://webb-database.com/ccass/bigchangesissue.asp?i=18546)
- [Concentration for issue 18546](https://webb-database.com/ccass/cconchist.asp?i=18546)

## Evidence

### Big Changes falsification: 08529

Treating the Big Changes Date as a trade date creates impossible observations:
the largest one-sided CCASS movement is greater than that day's exchange volume.
Treating it as a settlement date and moving back two XHKG sessions aligns it
with a sufficiently large trading day.

| Big Changes date | Largest one-sided move | Same-date volume | Implied trade date | Trade-date volume test |
| --- | ---: | ---: | --- | --- |
| 2026-05-11 | +2.88% | 2.15% | 2026-05-07 | 9.97%, plausible |
| 2026-05-19 | -1.06% | 0.57% | 2026-05-15 | 3.50%, plausible |
| 2026-05-22 | -0.55% | 0.20% | 2026-05-20 | 1.16%, plausible |

These three independent cases lock Big Changes to `settlement`.

### Changes cross-checks

The source page itself labels the table below the participant changes as
`Trades that settled in this date range` and supplies a separate `Trading date`.
Independent pages also preserve the two-session relationship:

| Issue | Queried/ending settlement date | Explicit Trading date | Largest named change | Trading-day volume |
| --- | --- | --- | ---: | ---: |
| 212 | 2026-05-07 | 2026-05-05 | -910,000 | 915,000 |
| 36197 | 2026-04-10 | 2026-04-08 | less than 19,100 | 19,100 |
| 10981 | 2026-03-11 | 2026-03-09 | -50,000 | 109,800 |
| 249 | 2026-05-29 | 2026-05-27 | -37,440,000 | 42,184,000 |

The one-sided changes are not greater than the explicitly linked trading-day
volume. This supports using the page's Trading date for normalized Changes rows,
while retaining the source settlement range in metadata.

## XHKG trading-calendar conversion

`utils/date_semantics.py` uses `pandas_market_calendars` calendar `XHKG` and
counts two actual exchange sessions. It therefore accounts for weekends and
Hong Kong market holidays. The supported application range is 1990-01-01 to
2035-12-31. Dates outside that range are not derived and produce a warning.

The bundled `data/hkex_holidays_fallback.csv` covers 2025-2027 only. It is used
only if `pandas_market_calendars` is unavailable, and that fallback is stated in
the warnings. The holiday source is the
[HKEX Trading Calendar and Holiday Schedule](https://www.hkex.com.hk/Services/Trading/Derivatives/Overview/Trading-Calendar-and-Holiday-Schedule?sc_lang=en).

Required regressions:

| Settlement date | Implied trade date |
| --- | --- |
| 2026-05-11 | 2026-05-07 |
| 2026-05-12 | 2026-05-08 |
| 2026-05-19 | 2026-05-15 |
| 2026-08-11 | 2026-08-07 |
| 2026-08-12 | 2026-08-10 |

## Cross-source warning

**Webb-site and DisclosureTracker may use different date conventions. Convert
both datasets to an explicit trade or settlement basis before joining them.
Joining their raw `Date` columns can manufacture a transaction that never
occurred.**

If the basis or calendar coverage is unknown, this tool leaves derived dates
blank and emits a data-quality warning. It never substitutes a calendar-day
guess.
