# Claude MCP Connector

This service exposes a Streamable HTTP MCP endpoint for Claude custom connectors.

## Connector URL

```text
https://webbsite-ccass-api.onrender.com/mcp
```

Add this URL in Claude:

```text
Settings -> Connectors -> Add custom connector
```

## Tool

```text
get_ccass_stock_data
```

Inputs:

```json
{
  "code": "01592",
  "holdings_limit": 20,
  "changes_limit": 30,
  "big_changes_limit": 20,
  "concentration_limit": 30
}
```

Only `code` is required. The MCP tool does not expose the API token or timeout to Claude. It calls the same internal parser used by the REST API and returns:

- `metadata`
- `holdings_summary`
- `holdings`
- `changes`
- `big_changes`
- `concentration`
- `fetch_summary`
- `data_quality_warnings`

## Verification

Local MCP client smoke test:

```bash
python -m uvicorn api:app --host 127.0.0.1 --port 8765
```

Connect to:

```text
http://127.0.0.1:8765/mcp
```

The public `robots.txt` explicitly allows `/mcp`, `/api/`, `/health`, and `/openapi.json`.

## Additional Tools

```text
get_webbsite_price_history
```

Inputs:

```json
{
  "code": "03321",
  "limit": 80
}
```

Returns metadata, latest price summary, recent price history rows, fetch summary, and data quality warnings.

```text
get_hkex_announcements
```

Inputs:

```json
{
  "code": "03321",
  "period_years": 1,
  "limit": 100
}
```

Returns HKEX stock metadata, announcement summary, and announcement rows with publish time, category, title, file type, URL, and news ID.
