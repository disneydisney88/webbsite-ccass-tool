from unittest.mock import Mock

import requests
import pytest

import api
from utils.longbridge import LongbridgeData, LongbridgeError
from utils.streamlit_longbridge import fetch_streamlit_longbridge


def test_bridge_keeps_all_holdings_and_does_not_read_local_credentials(monkeypatch):
    monkeypatch.setenv("CCASS_API_TOKEN", "fixture-token")
    monkeypatch.setenv("CCASS_RENDER_API_URL", "https://example.test")
    rows = [{"ccass_id": f"B{i:05}", "holding_shares": i} for i in range(145)]
    response = Mock()
    response.json.return_value = {"ok": True, "data": {
        "code": "01753", "data_date": "2026-09-04", "holdings": rows,
    }}
    get = Mock(return_value=response)
    monkeypatch.setattr("utils.streamlit_longbridge.requests.get", get)
    monkeypatch.setattr("utils.streamlit_longbridge.fetch_longbridge_stock",
                        Mock(side_effect=AssertionError("local credentials must not be used")))
    data = fetch_streamlit_longbridge("01753")
    assert data.holdings == rows
    assert data.data_date == "2026-09-04"
    assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer fixture-token"}


def test_bridge_http_failure_surfaces_without_secret(monkeypatch):
    monkeypatch.setenv("CCASS_API_TOKEN", "fixture-secret")
    monkeypatch.setenv("CCASS_RENDER_API_URL", "https://example.test")
    response = Mock(status_code=401)
    response.raise_for_status.side_effect = requests.HTTPError(response=response)
    monkeypatch.setattr("utils.streamlit_longbridge.requests.get", Mock(return_value=response))
    with pytest.raises(LongbridgeError, match="HTTP 401") as error:
        fetch_streamlit_longbridge("01753")
    assert "fixture-secret" not in str(error.value)


def test_server_bridge_is_authenticated_and_omits_raw_tool_payload(monkeypatch):
    monkeypatch.setattr(api, "fetch_longbridge_stock", lambda *args, **kwargs:
        LongbridgeData(code="01753", holdings=[{"holding_shares": 12}],
                       tool_results={"private_raw": "omit"}))
    route = next(r for r in api.app.routes if r.path == "/api/longbridge/stock")
    assert any(d.call is api.verify_api_token for d in route.dependant.dependencies)
    result = api.get_longbridge_stock_full("01753")
    assert result["data"]["holdings"] == [{"holding_shares": 12}]
    assert "tool_results" not in result["data"]
