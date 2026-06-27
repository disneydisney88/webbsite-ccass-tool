# ChatGPT / Custom GPT API

This project includes a read-only JSON API for Custom GPT Actions or other tools.

## Local Test

```bash
python api.py
```

Open:

- `http://localhost:8000/health`
- `http://localhost:8000/openapi.json`
- `http://localhost:8000/api/stock?stock_code=01592`
- `http://localhost:8000/api/stock?issue_id=26603`

## Required API Token

Set this environment variable on the server:

```text
API_TOKEN=<your-random-token>
```

The API service will not start if `API_TOKEN` is missing.

Protected `/api/*` endpoints must send:

```text
Authorization: Bearer <your-random-token>
```

Fallback for clients without Bearer support:

```text
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
GET /api/stock?stock_code=01592
```

It returns parsed Webb-site CCASS tables, price history, fetch summary, and a Markdown report suitable for analysis.
