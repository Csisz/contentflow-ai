import json
from pathlib import Path

from contentflow_ai.migration.cleanup import CleanupExecutor, CleanupPlanner
from contentflow_ai.migration.cli import main
from contentflow_ai.migration.config import MigrationConfig
from contentflow_ai.migration.reporter import ReportGenerator


def make_config() -> MigrationConfig:
    return MigrationConfig(
        base_url="https://example/otcs/cs.exe",
        username="technical_user",
        password="secret",
        enterprise_node_id=2000,
        template_id=111,
        wksp_type_id=222,
        category_id=123,
        ws_sheet="Workspace",
        file_sheet="File",
        ws_columns={"location": 0, "title": 1},
        file_columns={"location": 0, "title": 1, "src": 2},
        category_fields={},
        dry_run=False,
    )


class FakeCleanupClient:
    def __init__(self, fail_relation: bool = False, fail_workspace: bool = False):
        self.fail_relation = fail_relation
        self.fail_workspace = fail_workspace
        self.calls = []

    def authenticate(self):
        self.calls.append(("authenticate",))

    def delete_business_workspace_relation(self, bw_id: int, rel_bw_id: int, rel_type: str = "child"):
        self.calls.append(("delete_relation", bw_id, rel_bw_id, rel_type))
        if self.fail_relation:
            raise RuntimeError("relation delete failed")
        return True

    def delete_node(self, node_id: int):
        self.calls.append(("delete_node", node_id))
        if self.fail_workspace and node_id == 3001:
            raise RuntimeError("node delete failed")
        return True


def write_execution_report(path: Path, *, workspace_status: str = "created", workspace_node_id=3001):
    data = {
        "project_name": "ContentFlow AI",
        "mode": "execute",
        "workspaces": [
            {
                "row_index": 4,
                "excel_title": "DEV_TEST_001",
                "workspace_name": "SPLIC - 00143",
                "node_id": workspace_node_id,
                "status": workspace_status,
                "error": "",
            }
        ],
        "related_workspaces": [
            {
                "row_index": 2,
                "source_workspace": "DEV_TEST_001",
                "source_node_id": 3001,
                "source_resolved_name": "SPLIC - 00143",
                "target_workspace": "SPLIC - 00166",
                "target_node_id": 3166,
                "target_resolved_name": "SPLIC - 00166",
                "relation_type": "child",
                "status": "created",
                "error_message": "",
            }
        ],
        "files": [
            {
                "row_index": 8,
                "title": "example.pdf",
                "parent_node_id": 3001,
                "status": "uploaded",
            }
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def write_config(path: Path):
    path.write_text(json.dumps({
        "base_url": "https://example/otcs/cs.exe",
        "username": "technical_user",
        "password": "secret",
        "enterprise_node_id": 2000,
        "category_id": 123,
        "ws_sheet": "Workspace",
        "file_sheet": "File",
        "ws_columns": {"location": 0, "title": 1},
        "file_columns": {"location": 0, "title": 1, "src": 2},
        "category_fields": {},
    }), encoding="utf-8")
    return path


def test_cleanup_plan_reads_execution_report_and_lists_created_workspaces(tmp_path):
    report_path = write_execution_report(tmp_path / "execution.json")

    report = CleanupPlanner(make_config()).plan(report_path)

    assert report.mode == "cleanup-plan"
    assert report.workspaces[0].workspace_name == "SPLIC - 00143"
    assert report.workspaces[0].node_id == 3001
    assert report.workspaces[0].status == "planned"
    assert report.files[0].title == "example.pdf"


def test_cleanup_plan_lists_related_relations_but_does_not_delete_anything(tmp_path):
    report_path = write_execution_report(tmp_path / "execution.json")
    client = FakeCleanupClient()

    report = CleanupPlanner(make_config()).plan(report_path)

    assert report.relations[0].source_node_id == 3001
    assert report.relations[0].target_node_id == 3166
    assert client.calls == []


def test_cleanup_execute_requires_yes(tmp_path):
    config_path = write_config(tmp_path / "config.json")
    report_path = write_execution_report(tmp_path / "execution.json")

    exit_code = main(["--env-file", str(tmp_path / "missing.env"), "cleanup-execute", str(report_path), "--config", str(config_path)])

    assert exit_code == 2


def test_cleanup_execute_removes_related_relations_before_deleting_workspaces(tmp_path):
    report_path = write_execution_report(tmp_path / "execution.json")
    client = FakeCleanupClient()

    report = CleanupExecutor(make_config(), client=client).execute(report_path)

    assert report.stats.relations_removed == 1
    assert report.stats.workspaces_deleted == 1
    assert client.calls == [
        ("authenticate",),
        ("delete_relation", 3001, 3166, "child"),
        ("delete_node", 3001),
    ]


def test_cleanup_execute_does_not_delete_target_related_workspace_node(tmp_path):
    report_path = write_execution_report(tmp_path / "execution.json")
    client = FakeCleanupClient()

    CleanupExecutor(make_config(), client=client).execute(report_path)

    assert ("delete_node", 3166) not in client.calls


def test_cleanup_execute_skips_workspaces_not_marked_created(tmp_path):
    report_path = write_execution_report(tmp_path / "execution.json", workspace_status="existing")
    data = json.loads(report_path.read_text(encoding="utf-8"))
    data["workspaces"].append({
        "row_index": 5,
        "excel_title": "DEV_TEST_002",
        "workspace_name": "SPLIC - 00144",
        "node_id": 3002,
        "status": "created",
        "error": "",
    })
    report_path.write_text(json.dumps(data), encoding="utf-8")

    report = CleanupExecutor(make_config(), client=FakeCleanupClient()).execute(report_path)

    assert report.stats.workspaces_deleted == 1
    assert report.workspaces[0].status == "skipped"
    assert "not created" in report.workspaces[0].error_message


def test_cleanup_execute_refuses_when_report_has_no_created_workspace_node_ids(tmp_path):
    report_path = write_execution_report(tmp_path / "execution.json", workspace_status="existing", workspace_node_id="")

    try:
        CleanupExecutor(make_config(), client=FakeCleanupClient()).execute(report_path)
    except RuntimeError as exc:
        assert "no created workspace node IDs" in str(exc)
    else:
        raise AssertionError("cleanup should have refused to run")


def test_cleanup_execute_records_failures_and_continues(tmp_path):
    report_path = write_execution_report(tmp_path / "execution.json")
    client = FakeCleanupClient(fail_relation=True, fail_workspace=True)

    report = CleanupExecutor(make_config(), client=client).execute(report_path)

    assert report.stats.relations_failed == 1
    assert report.stats.workspaces_failed == 1
    assert report.relations[0].status == "failed"
    assert report.workspaces[0].status == "failed"


def test_cleanup_report_files_are_generated(tmp_path):
    report_path = write_execution_report(tmp_path / "execution.json")
    cleanup_report = CleanupPlanner(make_config()).plan(report_path)

    paths = ReportGenerator(tmp_path).write_cleanup_all(
        cleanup_report,
        execution_stem="execution",
        phase="plan",
        timestamp="20260604_120000",
    )

    assert set(paths) == {"json", "markdown"}
    assert paths["json"].name == "execution_cleanup_plan_20260604_120000.json"
    assert paths["markdown"].name == "execution_cleanup_plan_20260604_120000.md"
    for path in paths.values():
        assert path.exists()
