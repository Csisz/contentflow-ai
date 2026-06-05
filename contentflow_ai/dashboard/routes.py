from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, Response, redirect, render_template, request, send_file, url_for

from . import services

bp = Blueprint("dashboard", __name__)


@bp.get("/")
def index():
    return render_template(
        "index.html",
        cwd=str(Path.cwd()),
        config=services.config_status(),
        env=services.env_status(),
        excels=services.list_excel_files(),
        reports=services.latest_reports(),
        logs=services.latest_logs(),
        message=request.args.get("message", ""),
        result=None,
    )


@bp.post("/upload")
def upload():
    try:
        saved = services.save_upload(request.files.get("xlsx"))
        message = f"Uploaded: {saved}" if saved else "No file selected."
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
    return redirect(url_for("dashboard.index", message=message))


@bp.post("/action")
def action():
    action_name = request.form.get("action", "")
    xlsx = request.form.get("xlsx") or None
    report_json = request.form.get("report_json") or None
    validation = _validate_confirmation(action_name)
    if validation:
        return _render_index_with_result(message=validation)
    try:
        result = services.run_migration_action(action_name, xlsx=xlsx, report_json=report_json)
        return _render_index_with_result(result=result)
    except Exception as exc:  # noqa: BLE001
        return _render_index_with_result(message=str(exc))


@bp.get("/reports")
def reports():
    return render_template("reports.html", title="Reports", files=services.list_reports(), base_endpoint="dashboard.report_detail")


@bp.get("/logs")
def logs():
    return render_template("reports.html", title="Logs", files=services.list_logs(), base_endpoint="dashboard.report_detail")


@bp.get("/branding")
def branding():
    return render_template(
        "branding.html",
        title="Branding Settings",
        current=services.load_branding(),
        preview=None,
        message=request.args.get("message", ""),
    )


@bp.post("/branding")
def save_branding():
    data = {
        "app_title": request.form.get("app_title", ""),
        "app_subtitle": request.form.get("app_subtitle", ""),
        "logo_mode": request.form.get("logo_mode", "default"),
        "logo_path": request.form.get("logo_path", ""),
        "logo_url": request.form.get("logo_url", ""),
        "primary_color": request.form.get("primary_color", ""),
        "secondary_color": request.form.get("secondary_color", ""),
        "header_style": request.form.get("header_style", "simple"),
        "auto_detect_from_content_server": request.form.get("auto_detect_from_content_server") == "yes",
    }
    try:
        uploaded_logo = services.save_branding_logo(request.files.get("logo_file"))
        if uploaded_logo:
            data["logo_path"] = uploaded_logo
            data["logo_mode"] = "local"
        services.save_branding(data)
        message = "Branding settings saved."
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
    return redirect(url_for("dashboard.branding", message=message))


@bp.post("/branding/auto-detect")
def branding_auto_detect():
    detected = services.detect_content_server_branding(os.getenv("OTCS_BASE_URL"))
    current = services.load_branding()
    message = "Auto-detect preview ready."
    if detected.error:
        message = f"Auto-detect could not read Content Server branding: {detected.error}"
    return render_template(
        "branding.html",
        title="Branding Settings",
        current=current,
        preview=detected,
        message=message,
    )


@bp.get("/branding/logo")
def branding_logo():
    relative_path = request.args.get("path", "")
    try:
        return send_file(services.branding_logo_path(relative_path))
    except Exception as exc:  # noqa: BLE001
        return Response(str(exc), status=400, mimetype="text/plain")


@bp.get("/reports/open")
def report_detail():
    relative_path = request.args.get("path", "")
    try:
        path = services.safe_project_path(relative_path)
        if path.suffix.lower() in {".xlsx"}:
            return send_file(path, as_attachment=True)
        display_path, content = services.read_safe_text(relative_path)
        return render_template("report_detail.html", path=display_path, content=content)
    except Exception as exc:  # noqa: BLE001
        return Response(str(exc), status=400, mimetype="text/plain")


def _render_index_with_result(message: str = "", result=None):
    return render_template(
        "index.html",
        cwd=str(Path.cwd()),
        config=services.config_status(),
        env=services.env_status(),
        excels=services.list_excel_files(),
        reports=services.latest_reports(),
        logs=services.latest_logs(),
        message=message,
        result=result,
    )


def _validate_confirmation(action_name: str) -> str:
    if action_name == "execute" and request.form.get("confirm_execute") != "yes":
        return "Execution was not started. Confirm that you understand this will create or update Content Server objects."
    if action_name == "cleanup-execute" and request.form.get("confirm_cleanup") != "yes":
        return "Cleanup execute was not started. Confirm that you understand it will delete only objects listed in the selected execution report."
    return ""
