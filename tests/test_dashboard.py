from io import BytesIO
from pathlib import Path

import requests
from werkzeug.datastructures import FileStorage

from contentflow_ai.dashboard.app import create_app
from contentflow_ai.dashboard import services
from contentflow_ai.dashboard.services import (
    FileInfo,
    detect_content_server_branding,
    execution_report_options,
    list_excel_files,
    load_branding,
    report_type,
    save_branding_logo,
    workflow_state,
)


def test_flask_app_imports_successfully():
    app = create_app()

    assert app.name == "contentflow_ai.dashboard.app"


def test_dashboard_home_page_returns_200():
    app = create_app()

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"ContentFlow AI - Migration Copilot" in response.data


def test_dashboard_navigation_contains_settings_link():
    app = create_app()

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"Settings" in response.data
    assert b"/settings/branding" in response.data


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


def test_dashboard_report_type_detection_uses_filename_tokens():
    assert report_type("customer_analyze_report.json") == "analyze"
    assert report_type("content-server-preflight.json") == "preflight"
    assert report_type("migration_dry_run_2026.json") == "dry-run"
    assert report_type("final_execution.json") == "execution"
    assert report_type("cleanup_plan.json") == "cleanup"
    assert report_type("notes.json") == "unknown"


def test_dashboard_report_type_prefers_json_mode_over_execution_filename(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    dry_run = reports_dir / "migration_execution_20260605_100000.json"
    dry_run.write_text('{"mode": "dry-run", "xlsx_path": "uploads/migration.xlsx"}', encoding="utf-8")

    assert report_type("reports/migration_execution_20260605_100000.json", tmp_path) == "dry-run"


def test_dashboard_execution_report_options_exclude_dry_run_execution_files(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    dry_run = reports_dir / "migration_execution_20260605_100000.json"
    execute = reports_dir / "migration_execution_20260605_110000.json"
    dry_run.write_text('{"mode": "dry-run", "xlsx_path": "uploads/migration.xlsx"}', encoding="utf-8")
    execute.write_text('{"mode": "execute", "xlsx_path": "uploads/migration.xlsx"}', encoding="utf-8")
    reports = [
        _file_info("migration_execution_20260605_100000.json", "reports/migration_execution_20260605_100000.json"),
        _file_info("migration_execution_20260605_110000.json", "reports/migration_execution_20260605_110000.json"),
    ]

    options = execution_report_options(reports, tmp_path)

    assert [item.name for item in options] == ["migration_execution_20260605_110000.json"]


def test_dashboard_workflow_without_selected_workbook_starts_at_upload():
    state = workflow_state(None, [])

    assert state["current_key"] == "upload"
    assert _step_statuses(state)["upload"] == "current"
    assert _step_statuses(state)["analyze"] == "pending"


def test_dashboard_workflow_selected_workbook_without_reports_starts_at_analyze():
    workbook = _file_info("migration.xlsx", "uploads/migration.xlsx")

    state = workflow_state(workbook, [])

    statuses = _step_statuses(state)
    assert state["current_key"] == "analyze"
    assert statuses["upload"] == "complete"
    assert statuses["analyze"] == "current"
    assert statuses["execute"] == "pending"


def test_dashboard_workflow_preflight_only_makes_dry_run_current(tmp_path):
    workbook = _file_info("migration.xlsx", "uploads/migration.xlsx")
    report = _write_report(tmp_path, "migration_preflight.json", mode="preflight", workbook=workbook.path)

    state = workflow_state(workbook, [report], tmp_path)

    statuses = _step_statuses(state)
    assert state["current_key"] == "dry-run"
    assert statuses["preflight"] == "complete"
    assert statuses["dry-run"] == "current"
    assert statuses["execute"] == "pending"


def test_dashboard_workflow_dry_run_makes_execute_current_not_complete(tmp_path):
    workbook = _file_info("migration.xlsx", "uploads/migration.xlsx")
    report = _write_report(tmp_path, "migration_execution_20260605_100000.json", mode="dry-run", workbook=workbook.path)

    state = workflow_state(workbook, [report], tmp_path)

    statuses = _step_statuses(state)
    assert state["current_key"] == "execute"
    assert statuses["dry-run"] == "complete"
    assert statuses["execute"] == "current"
    assert statuses["reports"] == "available"


def test_dashboard_workflow_ignores_old_unrelated_reports(tmp_path):
    workbook = _file_info("current.xlsx", "uploads/current.xlsx")
    old_report = _write_report(tmp_path, "old_execution_20260605_100000.json", mode="execute", workbook="uploads/old.xlsx")

    state = workflow_state(workbook, [old_report], tmp_path)

    statuses = _step_statuses(state)
    assert state["current_key"] == "analyze"
    assert statuses["analyze"] == "current"
    assert statuses["execute"] == "pending"


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

    response = app.test_client().get("/settings/branding")

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


def _file_info(name: str, path: str, suffix: str | None = None) -> FileInfo:
    return FileInfo(name, path, suffix or Path(name).suffix.lower().lstrip(".") or "file", "2026.06.05. 10:00", 10)


def _write_report(tmp_path: Path, name: str, *, mode: str, workbook: str) -> FileInfo:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(exist_ok=True)
    path = reports_dir / name
    path.write_text(f'{{"mode": "{mode}", "xlsx_path": "{workbook}"}}', encoding="utf-8")
    return _file_info(name, f"reports/{name}", "json")


def _step_statuses(state: dict[str, object]) -> dict[str, str]:
    return {step["key"]: step["status"] for step in state["steps"]}
