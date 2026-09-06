from __future__ import annotations

from unittest.mock import patch

import utils.turso_db as turso_db


def test_turso_is_disabled_without_both_credentials() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert turso_db.turso_is_configured() is False
        assert turso_db.turso_health() == {"db_backend": "sqlite", "turso_ping_ms": None}


def test_libsql_url_uses_https_transport() -> None:
    assert turso_db.turso_http_url("libsql://example.turso.io") == "https://example.turso.io"
    assert turso_db.turso_http_url("https://example.turso.io") == "https://example.turso.io"


def test_turso_health_provisions_and_pings_without_exposing_credentials() -> None:
    calls: list[str] = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement: str):
            calls.append(statement)
            return object()

    with patch.dict(
        "os.environ",
        {"TURSO_DATABASE_URL": "libsql://example.turso.io", "TURSO_AUTH_TOKEN": "secret"},
        clear=True,
    ), patch.object(turso_db, "_create_client", return_value=FakeClient()):
        result = turso_db.turso_health()

    assert result["db_backend"] == "turso"
    assert isinstance(result["turso_ping_ms"], float)
    assert any(statement == "SELECT 1" for statement in calls)
    assert "secret" not in repr(result)
