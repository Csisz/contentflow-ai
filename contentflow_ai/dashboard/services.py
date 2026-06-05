from __future__ import annotations

import os
import json
from html.parser import HTMLParser
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename


PROJECT_ROOT = Path.cwd()
DEFAULT_CONFIG_PATH = Path("config") / "config.local.json"
BRANDING_LOCAL_PATH = Path("config") / "branding.local.json"
BRANDING_EXAMPLE_PATH = Path("config") / "branding.example.json"
BRANDING_DIR = Path("branding")
ALLOWED_TEXT_SUFFIXES = {".md", ".json", ".csv", ".txt", ".log"}
ALLOWED_LOGO_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
LOGO_MODES = {"default", "local", "url", "content_server_auto"}
HEADER_STYLES = {"simple", "compact", "opentext_like"}
DEFAULT_BRANDING = {
    "app_title": "ContentFlow AI",
    "app_subtitle": "Migration Copilot",
    "logo_mode": "default",
    "logo_path": "",
    "logo_url": "",
    "primary_color": "#206a5d",
    "secondary_color": "#20343a",
    "header_style": "simple",
    "auto_detect_from_content_server": False,
}


@dataclass(slots=True)
class CommandResult:
    command: list[str]
    return_code: int
    stdout: str
    stderr: str


@dataclass(slots=True)
class FileInfo:
    name: str
    path: str
    suffix: str
    modified: str
    size: int


@dataclass(slots=True)
class BrandingDetection:
    app_title: str
    logo_url: str
    favicon_url: str
    source_url: str
    error: str = ""


REPORT_TYPES = {
    "analyze": "Analyze",
    "preflight": "Preflight",
    "dry-run": "Dry-run",
    "dry_run": "Dry-run",
    "dryrun": "Dry-run",
    "execution": "Execution",
    "execute": "Execution",
    "cleanup": "Cleanup",
}

REPORT_TYPE_ORDER = ("analyze", "preflight", "dry-run", "execution", "cleanup")

WORKFLOW_STEPS = (
    ("upload", "Upload"),
    ("analyze", "Analyze"),
    ("preflight", "Preflight"),
    ("dry-run", "Dry-run"),
    ("execute", "Execute"),
    ("reports", "Reports"),
)


def active_config_path() -> Path:
    return Path(os.getenv("CONTENTFLOW_CONFIG_PATH", str(DEFAULT_CONFIG_PATH)))


def config_status() -> dict[str, str | bool]:
    path = active_config_path()
    return {"path": str(path), "exists": path.exists()}


def env_status() -> dict[str, bool]:
    return {
        "OTCS_BASE_URL": bool(os.getenv("OTCS_BASE_URL")),
        "OTCS_USERNAME": bool(os.getenv("OTCS_USERNAME")),
        "OTCS_PASSWORD": bool(os.getenv("OTCS_PASSWORD")),
    }


def load_branding(root: Path | None = None) -> dict[str, object]:
    root = root or PROJECT_ROOT
    branding = DEFAULT_BRANDING.copy()
    for relative_path in (BRANDING_EXAMPLE_PATH, BRANDING_LOCAL_PATH):
        path = root / relative_path
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            branding.update(_sanitize_branding(data))
    return branding


def save_branding(data: dict[str, object], root: Path | None = None) -> dict[str, object]:
    root = root or PROJECT_ROOT
    branding = DEFAULT_BRANDING.copy()
    branding.update(_sanitize_branding(data))
    path = root / BRANDING_LOCAL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(branding, indent=2) + "\n", encoding="utf-8")
    return branding


def save_branding_logo(file: FileStorage, root: Path | None = None) -> str | None:
    root = root or PROJECT_ROOT
    if not file or not file.filename:
        return None
    filename = secure_filename(file.filename)
    if not filename:
        raise ValueError("Logo filename is not valid.")
    if Path(filename).suffix.lower() not in ALLOWED_LOGO_SUFFIXES:
        raise ValueError("Only png, jpg, jpeg, svg and webp logo files are allowed.")
    branding_dir = root / BRANDING_DIR
    branding_dir.mkdir(parents=True, exist_ok=True)
    target = (branding_dir / filename).resolve()
    if not target.is_relative_to(branding_dir.resolve()):
        raise ValueError("Path traversal is not allowed.")
    file.save(target)
    return target.relative_to(root).as_posix()


def branding_logo_path(relative_path: str, root: Path | None = None) -> Path:
    root = (root or PROJECT_ROOT).resolve()
    branding_dir = (root / BRANDING_DIR).resolve()
    path = safe_project_path(relative_path, root)
    if not path.resolve().is_relative_to(branding_dir):
        raise ValueError("Only uploaded branding logos can be served.")
    if path.suffix.lower() not in ALLOWED_LOGO_SUFFIXES:
        raise ValueError("Unsupported logo file type.")
    return path


def logo_display(branding: dict[str, object]) -> dict[str, str]:
    mode = str(branding.get("logo_mode") or "default")
    logo_path = str(branding.get("logo_path") or "")
    logo_url = str(branding.get("logo_url") or "")
    if mode == "local" and logo_path:
        return {"mode": "local", "src": logo_path}
    if mode in {"url", "content_server_auto"} and logo_url:
        return {"mode": mode, "src": logo_url}
    return {"mode": "default", "src": ""}


def detect_content_server_branding(base_url: str | None) -> BrandingDetection:
    # Content Server appearance APIs are not part of the documented REST API in this project; auto-detect is best-effort.
    fallback = BrandingDetection(
        app_title=str(DEFAULT_BRANDING["app_title"]),
        logo_url="",
        favicon_url="",
        source_url=base_url or "",
        error="",
    )
    if not base_url:
        fallback.error = "OTCS_BASE_URL is not set."
        return fallback
    try:
        response = requests.get(base_url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        fallback.error = str(exc)
        return fallback

    parser = _BrandingHTMLParser()
    try:
        parser.feed(response.text)
    except Exception as exc:  # noqa: BLE001 - malformed vendor HTML should never break settings
        fallback.error = str(exc)
        return fallback

    title = parser.title.strip() or str(DEFAULT_BRANDING["app_title"])
    logo = _first_absolute_url(base_url, parser.logo_candidates)
    favicon = _first_absolute_url(base_url, parser.favicon_candidates)
    return BrandingDetection(
        app_title=title,
        logo_url=logo,
        favicon_url=favicon,
        source_url=base_url,
    )


def list_excel_files(root: Path | None = None) -> list[FileInfo]:
    root = root or PROJECT_ROOT
    files = [path for path in root.glob("*.xlsx") if path.is_file()]
    upload_dir = root / "uploads"
    if upload_dir.exists():
        files.extend(path for path in upload_dir.glob("*.xlsx") if path.is_file())
    return _file_infos(files, root)


def save_upload(file: FileStorage, root: Path | None = None) -> str | None:
    root = root or PROJECT_ROOT
    if not file or not file.filename:
        return None
    filename = secure_filename(file.filename)
    if Path(filename).suffix.lower() != ".xlsx":
        raise ValueError("Only .xlsx uploads are allowed.")
    upload_dir = root / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / filename
    file.save(target)
    return str(target.relative_to(root))


def list_reports(root: Path | None = None) -> list[FileInfo]:
    root = root or PROJECT_ROOT
    return _list_directory(root / "reports", root)


def list_logs(root: Path | None = None) -> list[FileInfo]:
    root = root or PROJECT_ROOT
    return _list_directory(root / "logs", root)


def latest_reports(root: Path | None = None, limit: int = 8) -> list[FileInfo]:
    return list_reports(root)[:limit]


def latest_logs(root: Path | None = None, limit: int = 8) -> list[FileInfo]:
    return list_logs(root)[:limit]


def selected_workbook(files: list[FileInfo], selected_path: str | None = None) -> FileInfo | None:
    if not selected_path:
        return None
    normalized = selected_path.replace("\\", "/")
    return next((file for file in files if file.path.replace("\\", "/") == normalized), None)


def report_type(filename: str, root: Path | None = None) -> str:
    metadata_type = _report_type_from_content(filename, root)
    if metadata_type:
        return metadata_type
    normalized = filename.lower().replace("-", "_").replace(" ", "_")
    for token, label in REPORT_TYPES.items():
        if token in normalized:
            return label.lower()
    return "unknown"


def report_summaries(files: list[FileInfo], root: Path | None = None) -> list[dict[str, object]]:
    return [
        {
            "file": file,
            "type": report_type(file.path, root),
            "is_latest": index == 0,
        }
        for index, file in enumerate(files)
    ]


def execution_report_options(files: list[FileInfo], root: Path | None = None) -> list[FileInfo]:
    return [file for file in files if file.suffix == "json" and report_type(file.path, root) == "execution"]


def workflow_state(selected: FileInfo | None, reports: list[FileInfo], root: Path | None = None) -> dict[str, object]:
    if selected is None:
        return _workflow_response(
            current_key="upload",
            summary="Select or upload a workbook to start the guided migration flow.",
            status_overrides={},
            matching_reports=[],
        )

    matching_reports = matching_workbook_reports(selected, reports, root)
    report_types = {report_type(file.path, root) for file in matching_reports}

    if "execution" in report_types:
        return _workflow_response(
            current_key="reports",
            summary="Execution output for the selected workbook is available. Review reports and logs before closing the run.",
            status_overrides={"execute": "complete"},
            matching_reports=matching_reports,
        )
    if "dry-run" in report_types:
        overrides = {"dry-run": "complete", "execute": "current", "reports": "available"}
        if "preflight" in report_types:
            overrides["preflight"] = "complete"
        else:
            overrides["preflight"] = "warning"
        return _workflow_response(
            current_key="execute",
            summary="Dry-run output exists for the selected workbook. Execute is available only after review and confirmation.",
            status_overrides=overrides,
            matching_reports=matching_reports,
        )
    if "preflight" in report_types:
        return _workflow_response(
            current_key="dry-run",
            summary="Preflight evidence exists for the selected workbook. Run a dry-run before controlled execution.",
            status_overrides={},
            matching_reports=matching_reports,
        )
    if "analyze" in report_types:
        return _workflow_response(
            current_key="preflight",
            summary="Analysis evidence exists for the selected workbook. Run Content Server preflight next.",
            status_overrides={},
            matching_reports=matching_reports,
        )
    return _workflow_response(
        current_key="analyze",
        summary="Workbook is selected. Start with Analyze to inspect the migration plan.",
        status_overrides={},
        matching_reports=matching_reports,
    )


def matching_workbook_reports(workbook: FileInfo, reports: list[FileInfo], root: Path | None = None) -> list[FileInfo]:
    workbook_stem = Path(workbook.name).stem.lower()
    workbook_name = workbook.name.lower()
    workbook_path = workbook.path.replace("\\", "/").lower()
    matches = []
    for report in reports:
        filename = report.name.lower()
        if filename.startswith(f"{workbook_stem}_") or filename == f"{workbook_stem}.json":
            matches.append(report)
            continue
        metadata = _report_metadata(report.path, root)
        xlsx_path = str(metadata.get("xlsx_path") or metadata.get("workbook") or "").replace("\\", "/").lower()
        if xlsx_path and (xlsx_path.endswith(f"/{workbook_name}") or xlsx_path == workbook_name or xlsx_path == workbook_path):
            matches.append(report)
    return matches


def read_safe_text(relative_path: str, root: Path | None = None) -> tuple[Path, str]:
    root = root or PROJECT_ROOT
    path = safe_project_path(relative_path, root)
    if path.suffix.lower() not in ALLOWED_TEXT_SUFFIXES:
        raise ValueError("This file type is not displayed as text.")
    return path, path.read_text(encoding="utf-8", errors="replace")


def readiness_preview(relative_path: str, content: str, root: Path | None = None) -> dict[str, object] | None:
    path = Path(relative_path)
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        summary = data.get("readiness") or data.get("summary")
        if not isinstance(summary, dict) or "readiness_score" not in summary:
            return None
        return {
            "readiness_score": summary.get("readiness_score"),
            "decision": summary.get("decision"),
            "total_issues": summary.get("total_issues"),
            "blocking_issue_count": summary.get("blocking_issue_count") or summary.get("blocking_count"),
            "warning_count": summary.get("warning_count"),
            "info_count": summary.get("info_count"),
            "top_risks": summary.get("top_risks") or [],
            "recommended_next_steps": summary.get("recommended_next_steps") or [],
        }
    if path.suffix.lower() == ".md" and "# Migration Readiness Report" in content:
        sibling = _report_json_sibling(safe_project_path(relative_path, root))
        if sibling and sibling.exists():
            try:
                data = json.loads(sibling.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                data = {}
            summary = data.get("readiness") or data.get("summary") if isinstance(data, dict) else None
            if isinstance(summary, dict):
                return {
                    "readiness_score": summary.get("readiness_score"),
                    "decision": summary.get("decision"),
                    "total_issues": summary.get("total_issues"),
                    "blocking_issue_count": summary.get("blocking_issue_count") or summary.get("blocking_count"),
                    "warning_count": summary.get("warning_count"),
                    "info_count": summary.get("info_count"),
                    "top_risks": summary.get("top_risks") or [],
                    "recommended_next_steps": summary.get("recommended_next_steps") or [],
                }
    return None


def safe_project_path(relative_path: str, root: Path | None = None) -> Path:
    root = (root or PROJECT_ROOT).resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Path traversal is not allowed.")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(relative_path)
    return path


def run_migration_action(action: str, *, xlsx: str | None = None, report_json: str | None = None) -> CommandResult:
    config_path = active_config_path()
    command = [sys.executable, "-m", "contentflow_ai.migration.cli", action]
    if action in {"analyze", "preflight", "dry-run", "execute"}:
        if not xlsx:
            raise ValueError("Select an Excel workbook.")
        workbook = safe_excel_reference(xlsx)
        command.extend([str(workbook), "--config", str(config_path)])
        if action == "execute":
            command.append("--yes")
    elif action in {"cleanup-plan", "cleanup-execute"}:
        if not report_json:
            raise ValueError("Select an execution report JSON.")
        report = safe_report_reference(report_json)
        command.extend([str(report), "--config", str(config_path)])
        if action == "cleanup-execute":
            command.append("--yes")
    else:
        raise ValueError(f"Unsupported action: {action}")
    completed = subprocess.run(command, capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=60 * 60)
    return CommandResult(command=command, return_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def safe_excel_reference(value: str, root: Path | None = None) -> Path:
    path = safe_project_path(value, root)
    if path.suffix.lower() != ".xlsx":
        raise ValueError("Only .xlsx files are allowed.")
    return path


def safe_report_reference(value: str, root: Path | None = None) -> Path:
    path = safe_project_path(value, root)
    project_root = (root or PROJECT_ROOT).resolve()
    if path.suffix.lower() != ".json" or report_type(str(path.relative_to(project_root)), project_root) != "execution":
        raise ValueError("Select an execution JSON report.")
    return path


def _list_directory(directory: Path, root: Path) -> list[FileInfo]:
    if not directory.exists():
        return []
    return _file_infos((path for path in directory.iterdir() if path.is_file()), root)


def _file_infos(paths: Iterable[Path], root: Path) -> list[FileInfo]:
    infos = []
    for path in paths:
        stat = path.stat()
        infos.append(
            FileInfo(
                name=path.name,
                path=str(path.relative_to(root)),
                suffix=path.suffix.lower().lstrip(".") or "file",
                modified=datetime.fromtimestamp(stat.st_mtime).strftime("%Y.%m.%d. %H:%M"),
                size=stat.st_size,
            )
        )
    return sorted(infos, key=lambda item: item.modified, reverse=True)


def _workflow_response(
    *,
    current_key: str,
    summary: str,
    status_overrides: dict[str, str],
    matching_reports: list[FileInfo],
) -> dict[str, object]:
    current_index = next(index for index, step in enumerate(WORKFLOW_STEPS) if step[0] == current_key)
    steps = []
    for index, (key, label) in enumerate(WORKFLOW_STEPS):
        status = status_overrides.get(key)
        if not status:
            if index < current_index:
                status = "complete"
            elif index == current_index:
                status = "current"
            else:
                status = "pending"
        steps.append({"key": key, "label": label, "status": status})
    return {
        "current_key": current_key,
        "summary": summary,
        "scope_note": "Progress is based on the selected workbook and its latest matching reports.",
        "matching_report_count": len(matching_reports),
        "steps": steps,
    }


def _report_type_from_content(relative_path: str, root: Path | None = None) -> str:
    metadata = _report_metadata(relative_path, root)
    mode = str(metadata.get("mode") or metadata.get("action") or metadata.get("command") or "").lower()
    if mode in {"analyze", "preflight", "dry-run", "dry_run", "dryrun", "execute", "execution"}:
        return "dry-run" if mode in {"dry-run", "dry_run", "dryrun"} else ("execution" if mode in {"execute", "execution"} else mode)
    if mode in {"cleanup-plan", "cleanup-execute", "cleanup-execution", "cleanup"}:
        return "cleanup"
    return ""


def _report_metadata(relative_path: str, root: Path | None = None) -> dict[str, object]:
    root = root or PROJECT_ROOT
    try:
        path = safe_project_path(relative_path, root)
    except (FileNotFoundError, ValueError):
        path = Path(relative_path)
        if not path.is_absolute():
            path = root / relative_path
    if not path.exists() or not path.is_file():
        return {}
    sibling_json = _report_json_sibling(path)
    if path.suffix.lower() != ".json" and sibling_json and sibling_json.exists():
        try:
            data = json.loads(sibling_json.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            return data
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}
    if path.suffix.lower() in {".md", ".txt", ".log"}:
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:4096].lower()
        except OSError:
            return {}
        for mode in REPORT_TYPE_ORDER:
            if f"mode:** {mode}" in head or f"mode: {mode}" in head or f'"mode": "{mode}"' in head:
                return {"mode": mode}
        if "mode:** execute" in head or "mode: execute" in head:
            return {"mode": "execute"}
    return {}


def _report_json_sibling(path: Path) -> Path | None:
    if path.suffix.lower() == ".json":
        return path
    direct = path.with_suffix(".json")
    if direct.exists():
        return direct
    if path.stem.endswith("_issues"):
        return path.with_name(path.stem.removesuffix("_issues") + ".json")
    return None


def _sanitize_branding(data: dict[str, object]) -> dict[str, object]:
    cleaned: dict[str, object] = {}
    for key in ("app_title", "app_subtitle", "logo_url", "primary_color", "secondary_color"):
        if key in data:
            cleaned[key] = str(data.get(key) or "").strip()
    if "logo_path" in data:
        cleaned["logo_path"] = _safe_logo_relative_path(str(data.get("logo_path") or "").strip())
    if data.get("logo_mode") in LOGO_MODES:
        cleaned["logo_mode"] = data["logo_mode"]
    if data.get("header_style") in HEADER_STYLES:
        cleaned["header_style"] = data["header_style"]
    if "auto_detect_from_content_server" in data:
        cleaned["auto_detect_from_content_server"] = bool(data["auto_detect_from_content_server"])
    for color_key in ("primary_color", "secondary_color"):
        value = str(cleaned.get(color_key) or "")
        if not _is_safe_color(value):
            cleaned.pop(color_key, None)
    return cleaned


def _safe_logo_relative_path(value: str) -> str:
    if not value:
        return ""
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return ""
    if not path.parts or path.parts[0] != BRANDING_DIR.name:
        return ""
    if path.suffix.lower() not in ALLOWED_LOGO_SUFFIXES:
        return ""
    return path.as_posix()


def _is_safe_color(value: str) -> bool:
    if not value:
        return False
    if value.startswith("#") and len(value) in {4, 7}:
        return all(char in "0123456789abcdefABCDEF" for char in value[1:])
    return value.replace("-", "").isalnum()


def _first_absolute_url(base_url: str, candidates: list[str]) -> str:
    for candidate in candidates:
        if candidate:
            return urljoin(base_url, candidate)
    return ""


class _BrandingHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_title = False
        self.title = ""
        self.logo_candidates: list[str] = []
        self.favicon_candidates: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "img":
            src = attr.get("src", "")
            alt = attr.get("alt", "")
            class_name = attr.get("class", "")
            marker = f"{src} {alt} {class_name}".lower()
            if src and any(token in marker for token in ("logo", "brand", "masthead")):
                self.logo_candidates.append(src)
        if tag.lower() == "link":
            rel = attr.get("rel", "").lower()
            href = attr.get("href", "")
            if href and "icon" in rel:
                self.favicon_candidates.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
