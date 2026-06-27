from __future__ import annotations

import json
import os
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import pandas as pd

from utils.exporters import parsed_to_json_ready
from utils.fetcher import IssueLookup, clean_stock_code, fetch_all, resolve_issue_id_from_stock
from utils.parser import build_fetch_summary, parse_results
from utils.report import build_report


API_TITLE = "Webb-site CCASS Research API"
API_VERSION = "1.0.0"
PUBLIC_PATHS = {"/", "/health", "/openapi.json"}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and (pd.isna(value) or value in {float("inf"), float("-inf")}):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def bool_param(params: dict[str, list[str]], name: str, default: bool = False) -> bool:
    raw = params.get(name, [str(default)])[0]
    return str(raw).lower() in {"1", "true", "yes", "y", "on"}


def int_param(params: dict[str, list[str]], name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(params.get(name, [str(default)])[0])
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def make_lookup_from_issue_id(issue_id: str, stock_code: str = "") -> IssueLookup:
    return IssueLookup(
        stock_code=stock_code,
        issue_id=issue_id,
        method="api issue_id parameter",
        status="success",
        message="Issue ID was provided by API caller.",
    )


def required_api_token() -> str:
    token = os.getenv("API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("API_TOKEN is not set")
    return token


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS


def valid_api_auth(path: str, authorization: str = "", x_api_key: str = "") -> bool:
    if is_public_path(path):
        return True
    if not path.startswith("/api/"):
        return False

    token = required_api_token()
    prefix = "Bearer "
    if authorization.startswith(prefix):
        supplied = authorization[len(prefix) :].strip()
        return secrets.compare_digest(supplied, token)
    if x_api_key:
        return secrets.compare_digest(x_api_key.strip(), token)
    return False


def fetch_stock_payload(params: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    timeout = int_param(params, "timeout", default=60, minimum=10, maximum=120)
    headless = bool_param(params, "headless", default=True)
    include_report = bool_param(params, "report", default=True)
    stock_code = clean_stock_code(params.get("stock_code", [""])[0])
    issue_id = re.sub(r"\D", "", params.get("issue_id", [""])[0])

    if not stock_code and not issue_id:
        return 400, {"error": "Provide stock_code or issue_id."}

    if stock_code:
        lookup = resolve_issue_id_from_stock(stock_code, timeout=timeout, headless=headless)
        if lookup.status != "success" or not lookup.issue_id:
            return 502, {"error": lookup.message or "Could not resolve Webb-site issue ID.", "lookup": lookup.__dict__}
        issue_id = lookup.issue_id
    else:
        lookup = make_lookup_from_issue_id(issue_id)

    results = fetch_all(issue_id, stock_code=stock_code, timeout=timeout, headless=headless)
    parsed = parse_results(
        issue_id,
        results,
        stock_code=lookup.stock_code or stock_code,
        id_lookup_method=lookup.method,
        id_lookup_status=lookup.status,
    )
    payload = parsed_to_json_ready(parsed, results)
    payload["fetch_summary_compact"] = build_fetch_summary(parsed, results).to_dict(orient="records")
    payload["report_markdown"] = build_report(parsed, results) if include_report else ""
    return 200, json_safe(payload)


def openapi_schema(base_url: str) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": API_TITLE,
            "version": API_VERSION,
            "description": "Read-only API for Webb-site CCASS, price history and research-ready summaries.",
        },
        "servers": [{"url": base_url.rstrip("/")}],
        "paths": {
            "/": {
                "get": {
                    "operationId": "apiRoot",
                    "summary": "API root",
                    "responses": {"200": {"description": "API metadata and links"}},
                }
            },
            "/health": {
                "get": {
                    "operationId": "healthCheck",
                    "summary": "Check API status",
                    "responses": {"200": {"description": "API is running"}},
                }
            },
            "/api/stock": {
                "get": {
                    "operationId": "getWebbsiteCcassStock",
                    "summary": "Fetch Webb-site CCASS and price history by HK stock code or Webb-site issue ID",
                    "parameters": [
                        {
                            "name": "stock_code",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                            "description": "HK stock code, e.g. 01592.",
                        },
                        {
                            "name": "issue_id",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                            "description": "Webb-site internal issue ID, e.g. 26603.",
                        },
                        {
                            "name": "report",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "boolean", "default": True},
                            "description": "Include Markdown report for ChatGPT analysis.",
                        },
                        {
                            "name": "timeout",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "default": 60, "minimum": 10, "maximum": 120},
                            "description": "Timeout per source page in seconds.",
                        },
                    ],
                    "responses": {
                        "200": {"description": "Parsed CCASS, price history and report data"},
                        "400": {"description": "Missing or invalid input"},
                        "401": {"description": "Missing or invalid API token"},
                        "502": {"description": "Source lookup or fetch failed"},
                    },
                    "security": [{"bearerAuth": []}, {"apiKeyAuth": []}],
                }
            },
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Use the API_TOKEN configured on the API server.",
                },
                "apiKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Key",
                    "description": "Fallback for clients that cannot send Bearer authentication.",
                },
            }
        },
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        params = parse_qs(parsed_url.query)
        if not self.authorized(parsed_url.path):
            self.send_json(401, {"detail": "Unauthorized"})
            return

        if parsed_url.path == "/":
            self.send_json(
                200,
                {
                    "ok": True,
                    "service": API_TITLE,
                    "version": API_VERSION,
                    "links": {"health": "/health", "openapi": "/openapi.json", "stock": "/api/stock"},
                },
            )
        elif parsed_url.path == "/health":
            self.send_json(200, {"ok": True, "service": API_TITLE, "version": API_VERSION})
        elif parsed_url.path == "/openapi.json":
            self.send_json(200, openapi_schema(self.base_url()))
        elif parsed_url.path == "/api/stock":
            status, payload = fetch_stock_payload(params)
            self.send_json(status, payload)
        else:
            self.send_json(404, {"error": "Not found", "paths": ["/health", "/openapi.json", "/api/stock"]})

    def authorized(self, path: str) -> bool:
        return valid_api_auth(
            path=path,
            authorization=self.headers.get("Authorization", ""),
            x_api_key=self.headers.get("X-API-Key", ""),
        )

    def base_url(self) -> str:
        forwarded_proto = self.headers.get("X-Forwarded-Proto")
        forwarded_host = self.headers.get("X-Forwarded-Host")
        if forwarded_proto and forwarded_host:
            return f"{forwarded_proto}://{forwarded_host}"
        host = self.headers.get("Host", f"localhost:{os.getenv('PORT', '8000')}")
        scheme = "https" if self.headers.get("X-Forwarded-Ssl") == "on" else "http"
        return f"{scheme}://{host}"

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(json_safe(payload), ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        if os.getenv("API_QUIET", "0").lower() in {"1", "true", "yes"}:
            return
        super().log_message(format, *args)


def main() -> None:
    required_api_token()
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"{API_TITLE} listening on http://0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
