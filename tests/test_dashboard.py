from io import BytesIO
from pathlib import Path

import requests
from werkzeug.datastructures import FileStorage

from contentflow_ai.dashboard.app import create_app
from contentflow_ai.dashboard import services
from contentflow_ai.dashboard.services import (
    detect_content_server_branding,
    list_excel_files,
    load_branding,
    save_branding_logo,
)


def test_flask_app_imports_successfully():
    app = create_app()

    assert app.name == "contentflow_ai.dashboard.app"


def test_dashboard_home_page_returns_200():
    app = create_app()

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"ContentFlow AI - Migration Copilot" in response.data


def test_dashboard_reports_page_returns_200():
    app = create_app()

    response = app.test_client().get("/reports")

    assert response.status_code == 200
    assert b"Reports" in response.data


def test_excel_file_listing_ignores_non_xlsx_files(tmp_path):
    (tmp_path / "migration.xlsx").write_bytes(b"fake")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")
    (tmp_path / "migration.xls").write_bytes(b"ignore")

    files = list_excel_files(tmp_path)

    assert [item.name for item in files] == ["migration.xlsx"]


def test_execute_route_refuses_without_confirmation_checkbox():
    app = create_app()

    response = app.test_client().post("/action", data={"action": "execute", "xlsx": "migration.xlsx"})

    assert response.status_code == 200
    assert b"Execution was not started" in response.data


def test_cleanup_execute_route_refuses_without_confirmation_checkbox():
    app = create_app()

    response = app.test_client().post(
        "/action",
        data={"action": "cleanup-execute", "report_json": "reports/example_execution.json"},
    )

    assert response.status_code == 200
    assert b"Cleanup execute was not started" in response.data


def test_dashboard_does_not_display_secret_values(monkeypatch):
    monkeypatch.setenv("OTCS_PASSWORD", "super-secret-password")
    app = create_app()

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"super-secret-password" not in response.data
    assert b"OTCS_PASSWORD" in response.data


def test_branding_defaults_load_without_config_files(tmp_path):
    branding = load_branding(tmp_path)

    assert branding["app_title"] == "ContentFlow AI"
    assert branding["app_subtitle"] == "Migration Copilot"
    assert branding["logo_mode"] == "default"


def test_branding_local_overrides_defaults(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "branding.local.json").write_text(
        '{"app_title": "Local Title", "primary_color": "#112233"}',
        encoding="utf-8",
    )

    branding = load_branding(tmp_path)

    assert branding["app_title"] == "Local Title"
    assert branding["app_subtitle"] == "Migration Copilot"
    assert branding["primary_color"] == "#112233"


def test_base_page_displays_configured_branding(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "branding.local.json").write_text(
        '{"app_title": "Client Migration", "app_subtitle": "Local Control"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(services, "PROJECT_ROOT", tmp_path)
    app = create_app()

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"Client Migration" in response.data
    assert b"Local Control" in response.data


def test_branding_settings_page_returns_200():
    app = create_app()

    response = app.test_client().get("/branding")

    assert response.status_code == 200
    assert b"Branding Settings" in response.data


def test_invalid_logo_extension_is_rejected(tmp_path):
    logo = FileStorage(stream=BytesIO(b"bad"), filename="logo.exe")

    try:
        save_branding_logo(logo, tmp_path)
    except ValueError as exc:
        assert "logo files are allowed" in str(exc)
    else:
        raise AssertionError("Expected invalid logo extension to be rejected.")


def test_path_traversal_upload_name_is_sanitized(tmp_path):
    logo = FileStorage(stream=BytesIO(b"fake"), filename="../client-logo.png")

    saved = save_branding_logo(logo, tmp_path)

    assert saved == "branding/client-logo.png"
    assert (tmp_path / "branding" / "client-logo.png").exists()
    assert not (tmp_path.parent / "client-logo.png").exists()


def test_auto_detect_failure_falls_back_safely(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("offline")

    monkeypatch.setattr(services.requests, "get", fail_get)

    detected = detect_content_server_branding("https://content-server.example.com")

    assert detected.app_title == "ContentFlow AI"
    assert detected.logo_url == ""
    assert "offline" in detected.error
