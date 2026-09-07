"""Streamlit delegates authenticated Longbridge reads to its configured API."""
import os

import requests

from .longbridge import LongbridgeData, LongbridgeError, fetch_longbridge_stock


def fetch_streamlit_longbridge(code: str, timeout: float = 30) -> LongbridgeData:
    token = os.getenv("CCASS_API_TOKEN", "").strip()
    base = os.getenv("CCASS_RENDER_API_URL", "").strip().rstrip("/")
    if not token or not base:
        return fetch_longbridge_stock(code, timeout=timeout)
    try:
        response = requests.get(
            f"{base}/api/longbridge/stock",
            params={"code": code},
            headers={"Authorization": f"Bearer {token}"},
            timeout=(10, max(60, timeout)),
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise LongbridgeError(str(payload.get("error") or "Render Longbridge request failed."))
        data = payload["data"]
        return LongbridgeData(**{
            key: data[key] for key in LongbridgeData.__dataclass_fields__
            if key in data and key != "tool_results"
        })
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        raise LongbridgeError(
            f"Render Longbridge bridge unavailable: {f'HTTP {status}' if status else type(exc).__name__}. "
            "Check CCASS_API_TOKEN in Streamlit Secrets and Render service health."
        ) from exc
