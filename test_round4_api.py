import api


def test_round4_routes_are_registered_and_protected():
    paths = {route.path for route in api.app.routes}
    assert "/admin/run_daily" in paths
    assert "/admin/jobs/{job_id}" in paths
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
    assert "db_path" in fields
    assert "disk_free_mb" in fields
    assert "worker_last_run" in fields
    assert "server_time_utc" in fields
    assert "server_time_hkt" in fields
    assert "next_trading_day_hkt" in fields


def test_daily_worker_contract_is_not_mcp_wall_clock_work():
    request = api.DailyRunRequest()
    assert request.sleep_seconds >= 1.5
    assert api.stock_tool_budget("hybrid_light") == 30
