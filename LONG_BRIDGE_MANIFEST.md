# CCASS Tool Round 3 Longbridge Manifest

Generated: 2026-09-04

## Authentication Route

The selected primary route is OAuth 2.0 Device Authorization against Longbridge
OpenAPI. The server dynamically registers and reuses a client, returns only the
verification URL/user code/opaque session ID, and stores device state, access
tokens, and refresh tokens encrypted with `LONGBRIDGE_TOKEN_KEY`.

Agent Auth Code over MCP Streamable HTTP remains Route B. Longbridge's official
documentation states that this one-use code is valid for 10 minutes; the revised
external brief's five-minute statement conflicts with that primary source.
Official SDK OAuth on a trusted workstation remains Route C and is not imported
as a plaintext server credential.

No live account authorization was supplied. Token lifetime, refresh availability,
expiry HTTP response, and production account-region eligibility are therefore
**unknown / unconfirmed**. Device-flow expiry and refresh rotation are covered
by offline fixtures.

## Percentage Denominator

The 06182 fixture uses 540,928,000 shares at 67.616%, implying approximately
800,000,000 issued shares. `stake_pct_of_ccass` is independently calculated
from the sum of participant shares. Live denominator verification is pending.

## Acceptance Status

1. Device start/poll, refresh, and health expiry fields: **PASS (fixture)**;
   live account acceptance remains blocked.
2. 06182 live Holdings >=100 rows: **BLOCKED**, fresh code required.
3. Dual denominator fields: **PASS (fixture)**, live check blocked.
4. Derived concentration: **PASS (fixture)**, live check blocked.
5. B01438 daily live history: **BLOCKED**, fresh code required.
6. Encrypted/corrupt-token handling: **PASS (fixture)**.
7. Four stock-symbol conversions: **PASS**.
8. ISO date normalization: **PASS (fixture)**, live check blocked.
9. Read-only whitelist: **PASS**.
10. `caiji` snapshot run without rate limiting: **BLOCKED**, live account authorization required.

Offline verification: `python -m unittest discover` ran 209 tests successfully.

## File Hashes

SHA-256 values for the current uncommitted implementation:

| File | SHA-256 |
|---|---|
| `api.py` | `dd78e3125a989c1594eea253c0756700f58bf1ecf96d2a3eec11d6e5a51fa169` |
| `app.py` | `9ca98a573ee9db20f7bcbad1903fbad0fb77240caa69f1c98644ebae03d89223` |
| `utils/longbridge.py` | `327fec5e109c7588320ebf8eb793668a3664786a25ee505621e980d9f89a4810` |
| `utils/snapshot_db.py` | `9701977e6dad044cd6df7e8e6a2b3b1a254a94e01b114a9c16272f9c84eb6d8c` |
| `test_longbridge.py` | `8d8b30699f39a6750f568977299440921bb5e9ddf87e63417baec3d66b9aa570` |
| `requirements.txt` | `d781987711ebf0e9c2dd735a530fafcb2af05f9742f4399e7d113ee1cd7f8d93` |
| `README.md` | `f6ddf5ca85b4d2b4380bcaaeafed22140384ac0cc0cf160b7a2ef51249ff75c5` |
| `API_README.md` | `79808a247f159fb59c4c9eccc3569548424dbe236ab8e402726ee810c6b98e1a` |
| `HANDOVER.md` | `73064cd1ceca054dc66cecaf880b7e81435f971b35288de6ab86334735b01ad3` |
| `LONG_BRIDGE_CHANGELOG.md` | `34fd202de2a9c207db2a75bcf670dae6503ec46b53e5f6ea0a1cd4b6846c4687` |
| `06182_longbridge_20260904.json` | `c6b69582d3272504583a23f65a52e6774992112cf930be758c5eae9c634a3cfc` |
| `06182_cross_check_20260904.json` | `a96944264755b527681c214994809b8c57930c7284074106ceda1dab374efb1d` |
| `06182_B01438_daily.json` | `723aaa143ab7aec4d9df570355ccaf8dd32cd4003dfb6af663f433bd79ff39fb` |

Recompute these hashes after any review edit or commit-time conflict resolution.
