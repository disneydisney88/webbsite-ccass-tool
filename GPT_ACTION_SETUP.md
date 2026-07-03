# Custom GPT Action Setup

Use the Streamlit site for human browsing and deploy the API service for GPT Actions.

## Recommended Deployment

Deploy this repository to Render using `render.yaml`.

It creates two services:

- `webbsite-ccass-tool`: Streamlit UI
- `webbsite-ccass-api`: JSON API for Custom GPT

Set an `API_TOKEN` secret on the API service only if you want to protect the API. If `API_TOKEN` is not set, `/api/stock` remains public and read-only.

## API URLs

After deployment, test:

```text
https://your-api-service.onrender.com/health
https://your-api-service.onrender.com/openapi.json
https://your-api-service.onrender.com/api/stock?code=01592
https://your-api-service.onrender.com/api/stock?code=01592&timeout=30&holdings_limit=20&changes_limit=30&big_changes_limit=20&concentration_limit=30
```

## Custom GPT Instructions

Paste this into your GPT instructions:

```text
When the user asks for Hong Kong stock CCASS analysis, use the Webb-site CCASS API action.

Call getWebbsiteCcassStock with code when the user gives a HK stock code, for example 01592.

Use the returned metadata, holdings_summary, holdings, changes, big_changes, concentration, fetch_summary and data_quality_warnings.

Always distinguish facts from inference.
CCASS is T+2 data, so do not describe it as same-day holdings.
Do not treat a single broker decrease as distribution unless price, turnover, volume, VWAP, concentration and announcements support that inference.
For price analysis, compare latest close with Daily VWAP and turnover/volume.
```

## Custom GPT Action Schema

In the GPT builder, add an Action and import:

```text
https://your-api-service.onrender.com/openapi.json
```

If `API_TOKEN` is set and your client cannot send custom headers, include it in the URL as `key=<token>` or `api_token=<token>`.
Bearer token and `X-API-Key` headers are also accepted for clients that support headers.

## Why This Is Separate From Streamlit

Streamlit Cloud is good for the interactive UI, but Custom GPT Actions need a stable HTTPS JSON API with an OpenAPI schema. The API service provides that through `/openapi.json` and `/api/stock`.
