# Custom GPT Action Setup

Use the Streamlit site for human browsing and deploy the API service for GPT Actions.

## Recommended Deployment

Deploy this repository to Render using `render.yaml`.

It creates two services:

- `webbsite-ccass-tool`: Streamlit UI
- `webbsite-ccass-api`: JSON API for Custom GPT

Set an `API_TOKEN` secret on the API service if you want private access.

## API URLs

After deployment, test:

```text
https://your-api-service.onrender.com/health
https://your-api-service.onrender.com/openapi.json
https://your-api-service.onrender.com/api/stock?stock_code=01592
```

## Custom GPT Instructions

Paste this into your GPT instructions:

```text
When the user asks for Hong Kong stock CCASS analysis, use the Webb-site CCASS API action.

Call getWebbsiteCcassStock with stock_code when the user gives a HK stock code, for example 01592.
Use issue_id only when the user gives a Webb-site internal issue ID.

Use the returned holdings, changes, bigchanges, concentration, price_history, fetch_summary_compact and report_markdown.

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

If `API_TOKEN` is set, configure authentication as Bearer token and paste the same token.

## Why This Is Separate From Streamlit

Streamlit Cloud is good for the interactive UI, but Custom GPT Actions need a stable HTTPS JSON API with an OpenAPI schema. The API service provides that through `/openapi.json` and `/api/stock`.
