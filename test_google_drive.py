from __future__ import annotations

from utils.google_drive import brief_to_markdown, drive_config_status, holdings_to_csv, upload_brief_artifacts


def test_drive_config_status_does_not_expose_secret(monkeypatch, tmp_path):
    secret = tmp_path / "service-account.json"
    secret.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GDRIVE_SA_FILE", str(secret))
    monkeypatch.setenv("GDRIVE_FOLDER_ID", "folder-id")

    status = drive_config_status()

    assert status == {
        "configured": True,
        "service_account_file_set": True,
        "service_account_file_exists": True,
        "folder_id_set": True,
    }
    assert "folder-id" not in str(status)


def test_unconfigured_drive_is_non_fatal(monkeypatch):
    monkeypatch.delenv("GDRIVE_SA_FILE", raising=False)
    monkeypatch.delenv("GDRIVE_FOLDER_ID", raising=False)

    result = upload_brief_artifacts({"brief_date": "2026-09-06"})

    assert result["status"] == "not_configured"
    assert result["files"] == []


def test_brief_drive_upload_upserts_json_and_markdown(monkeypatch, tmp_path):
    secret = tmp_path / "service-account.json"
    secret.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GDRIVE_SA_FILE", str(secret))
    monkeypatch.setenv("GDRIVE_FOLDER_ID", "folder-id")
    calls = []
    monkeypatch.setattr("utils.google_drive._drive_service", lambda: object())

    def fake_upsert(service, folder_id, name, content, mime_type):
        calls.append((folder_id, name, content, mime_type))
        return {"file_id": name + "-id", "name": name, "folder_id": folder_id, "url": "https://drive.test/" + name}

    monkeypatch.setattr("utils.google_drive._upsert_drive_file", fake_upsert)
    payload = {
        "brief_date": "2026-09-06",
        "data_date": "2026-09-06",
        "trade_date_covered": "2026-09-04",
        "coverage": {"lshape79": 79},
        "signals": {"S1": [], "S2": []},
    }

    result = upload_brief_artifacts(payload)

    assert result["status"] == "success"
    assert [call[1] for call in calls] == ["brief_20260906.json", "brief_20260906.md"]
    assert calls[0][3] == "application/json"
    assert calls[1][3] == "text/markdown"
    assert len(result["files"]) == 2


def test_daily_holdings_csv_uses_utf8_sig_and_stable_columns():
    content = holdings_to_csv([{
        "code": "06182",
        "data_date": "2026-09-04",
        "participant_name": "金利豐",
        "holding_shares": 540900000,
    }])

    assert content.startswith("code,data_date,")
    assert "金利豐" in content


def test_brief_markdown_is_factual_and_utf8_safe():
    markdown = brief_to_markdown({
        "brief_date": "2026-09-06",
        "data_date": "2026-09-06",
        "trade_date_covered": "2026-09-04",
        "coverage": {"lshape79": 79},
        "signals": {"S1": [{"name": "金利豐"}]},
    })

    assert "# CCASS research brief 2026-09-06" in markdown
    assert "金利豐" not in markdown
    assert "| S1 | 1 |" in markdown
