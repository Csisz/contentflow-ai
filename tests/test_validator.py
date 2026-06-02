import json

from openpyxl import Workbook

from contentflow_ai.migration.config import load_config
from contentflow_ai.migration.excel_parser import parse_workbook
from contentflow_ai.migration.validator import PreflightValidator


def test_validator_flags_missing_file_and_duplicate_target(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Workspace"
    ws.append(["location", "title", "doctype"])
    ws.append(["Enterprise/Test", "WS-001", "Contract"])
    files = wb.create_sheet("File")
    files.append(["location", "title", "src", "mime"])
    files.append(["Enterprise/Test/WS-001", "doc.pdf", "missing.pdf", "pdf"])
    files.append(["Enterprise/Test/WS-001", "doc.pdf", "missing.pdf", "pdf"])
    xlsx = tmp_path / "migration.xlsx"
    wb.save(xlsx)

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "base_url": "https://example/otcs/cs.exe",
        "username": "technical_user",
        "password": "secret",
        "enterprise_node_id": 2000,
        "category_id": 123,
        "ws_sheet": "Workspace",
        "file_sheet": "File",
        "ws_data_start_row": 1,
        "file_data_start_row": 1,
        "ws_columns": {"location": 0, "title": 1},
        "file_columns": {"location": 0, "title": 1, "src": 2, "mime": 3},
        "category_fields": {"doctype": {"attr_id": 3, "col": 2, "required": True}}
    }), encoding="utf-8")
    cfg = load_config(config_path)
    workbook = parse_workbook(xlsx, cfg)

    report = PreflightValidator(cfg).validate(workbook)
    codes = {issue.code for issue in report.issues}

    assert "MISSING_SOURCE_FILE" in codes
    assert "DUPLICATE_FILE_IN_TARGET" in codes
    assert report.summary.decision == "NO_GO"
    assert report.summary.readiness_score < 100
