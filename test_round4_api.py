import json

import api


def test_round4_routes_are_registered_and_protected():
    paths = {route.path for route in api.app.routes}
    delete_routes = {(route.path, method) for route in api.app.routes for method in getattr(route, "methods", set())}
    assert "/admin/run_daily" in paths
    assert "/admin/jobs/{job_id}" in paths
    assert "/admin/job/{job_id}/cancel" in paths
    assert ("/admin/jobs/{job_id}", "DELETE") in delete_routes
    assert "/timeline" in paths
    assert "/panel/broker_daily" in paths
    assert "/panel/transfers" in paths
    assert "/brief/latest" in paths
    assert "/brief/{brief_date}" in paths
    assert "/hypotheses" in paths


def test_health_model_exposes_round4_operational_fields():
    fields = api.HealthResponse.model_fields
    assert "db_backend" in fields
    assert "turso_ping_ms" in fields
    assert "turso_last_batch_ms" in fields
    assert "db_path" in fields
    assert "disk_free_mb" in fields
    assert "worker_last_run" in fields
    assert "server_time_utc" in fields
    assert "server_time_hkt" in fields
    assert "next_trading_day_hkt" in fields


def test_daily_worker_contract_is_not_mcp_wall_clock_work():
    request = api.DailyRunRequest()
    assert request.sleep_seconds >= 1.5
    assert request.groups == ["lshape79", "caiji"]
    assert request.force is False
    assert api.DailyRunRequest(force=True).force is True
    assert api.stock_tool_budget("hybrid_light") == 30


def test_run_daily_holiday_skips_before_job_creation(monkeypatch):
    monkeypatch.setattr(api, "trading_sessions_between", lambda start, end: ([], ""))
    monkeypatch.setattr(api, "next_trading_date", lambda value: ("2026-09-08", ""))
    response = api.run_daily_endpoint(api.DailyRunRequest(run_date="2026-09-07"))
    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["status"] == "skipped_holiday"
    assert payload["next_trading_day"] == "2026-09-08"
    assert payload["accepted"] is False
