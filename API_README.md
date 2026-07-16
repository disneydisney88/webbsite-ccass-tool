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

## CHANGELOG

Breaking or behavioural changes are recorded here. Existing field names are kept
for at least one version when new fields are added, because downstream analysis
flows depend on the current schema.

### Unreleased

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
