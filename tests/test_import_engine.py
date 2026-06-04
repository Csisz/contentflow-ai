from pathlib import Path

from contentflow_ai.migration.config import MigrationConfig
from contentflow_ai.migration.cs_client import CSClientError
from contentflow_ai.migration.import_engine import ImportEngine, ImportExecutionReport
from contentflow_ai.migration.models import FileRow, MigrationWorkbook, RelatedWorkspaceRow, WorkspaceRow
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


class FakeImportClient:
    def __init__(self):
        self.ws_name_map = {}
        self.ws_node_id_map = {}
        self.resolved_paths = []
        self.upload_parent_ids = []
        self.relations = set()
        self.relation_posts = []
        self._next_ws_id = 3001

    def authenticate(self) -> None:
        return None

    def resolve_or_create_path(self, location: str) -> int:
        self.resolved_paths.append(location)
        return len(self.resolved_paths) + 100

    def create_or_get_workspace(self, parent_id: int, excel_name: str, cat_values: dict):
        node_id = self.ws_node_id_map.get(excel_name)
        if node_id is None:
            node_id = self._next_ws_id
            self._next_ws_id += 1
        actual_name = f"SPLIC - {node_id - 2858:05d}"
        self.ws_name_map[excel_name] = actual_name
        self.ws_node_id_map[excel_name] = node_id
        self.ws_node_id_map[actual_name] = node_id
        return node_id, True, actual_name

    def remap_file_location(self, location: str) -> str:
        for excel_name, actual_name in self.ws_name_map.items():
            location = location.replace(excel_name, actual_name)
        return location

    def upload_file(self, parent_id: int, name: str, local_path: str, mime_hint: str = "") -> str:
        self.upload_parent_ids.append(parent_id)
        return "uploaded"

    def resolve_workspace_node_id(self, workspace: str) -> int | None:
        if str(workspace).isdigit():
            return int(workspace)
        return self.ws_node_id_map.get(workspace)

    def relation_exists(self, bw_id: int, rel_bw_id: int, rel_type: str = "child") -> bool:
        return (bw_id, rel_bw_id, rel_type) in self.relations

    def add_business_workspace_relation(self, bw_id: int, rel_bw_id: int, rel_type: str = "child"):
        self.relation_posts.append((bw_id, rel_bw_id, rel_type))
        self.relations.add((bw_id, rel_bw_id, rel_type))
        return {"results": {"ok": True}}


class FakeFailingWorkspaceClient(FakeImportClient):
    def create_or_get_workspace(self, parent_id: int, excel_name: str, cat_values: dict):
        raise CSClientError(f"Business Workspace creation failed for '{excel_name}' HTTP 500: failed")


class FakeFailingFileClient(FakeImportClient):
    def upload_file(self, parent_id: int, name: str, local_path: str, mime_hint: str = "") -> str:
        self.upload_parent_ids.append(parent_id)
        return "failed"


def test_import_engine_uploads_file_under_remapped_workspace_path():
    client = FakeImportClient()
    engine = ImportEngine(make_config(), client=client)
    workbook = MigrationWorkbook(
        xlsx_path=Path("migration.xlsx"),
        workspaces=[
            WorkspaceRow(
                row_index=4,
                location=r"MIGR\Client",
                title="DEV_TEST_001",
                cat_values={},
            )
        ],
        files=[
            FileRow(
                row_index=2,
                location=r"MIGR\Client\DEV_TEST_001\03_Other documents",
                title="example.txt",
                local_path=r"C:\files\example.txt",
            )
        ],
        sheet_names=["Workspace", "File"],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.f_uploaded == 1
    assert client.resolved_paths == [
        r"MIGR\Client",
        r"MIGR\Client\SPLIC - 00143\03_Other documents",
    ]
    assert client.upload_parent_ids == [102]
    assert (
        r"File location remapped: MIGR\Client\DEV_TEST_001\03_Other documents "
        r"-> MIGR\Client\SPLIC - 00143\03_Other documents"
    ) in stats.details
    assert stats.workspace_rows[0].row_index == 4
    assert stats.workspace_rows[0].excel_title == "DEV_TEST_001"
    assert stats.workspace_rows[0].workspace_name == "SPLIC - 00143"
    assert stats.workspace_rows[0].node_id == 3001
    assert stats.workspace_rows[0].status == "created"
    assert stats.file_rows[0].row_index == 2
    assert stats.file_rows[0].title == "example.txt"
    assert stats.file_rows[0].source_path == r"C:\files\example.txt"
    assert stats.file_rows[0].original_target_location == r"MIGR\Client\DEV_TEST_001\03_Other documents"
    assert stats.file_rows[0].remapped_target_location == r"MIGR\Client\SPLIC - 00143\03_Other documents"
    assert stats.file_rows[0].parent_node_id == 102
    assert stats.file_rows[0].status == "uploaded"


def test_import_engine_related_workspace_resolves_placeholders_and_defaults_to_child():
    client = FakeImportClient()
    engine = ImportEngine(make_config(), client=client)
    workbook = MigrationWorkbook(
        xlsx_path=Path("migration.xlsx"),
        workspaces=[
            WorkspaceRow(row_index=4, location=r"MIGR\Client", title="DEV_TEST_001"),
            WorkspaceRow(row_index=5, location=r"MIGR\Client", title="DEV_TEST_002"),
        ],
        files=[],
        sheet_names=["Workspace", "File", "RelatedWorkspace"],
        related_workspaces=[
            RelatedWorkspaceRow(
                row_index=2,
                source_workspace="DEV_TEST_001",
                target_workspace="DEV_TEST_002",
                enabled=True,
            )
        ],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.related_created == 1
    assert client.relation_posts == [(3001, 3002, "child")]
    assert stats.related_rows[0].source_node_id == 3001
    assert stats.related_rows[0].target_node_id == 3002
    assert stats.related_rows[0].relation_type == "child"
    assert stats.related_rows[0].status == "created"


def test_import_engine_related_workspace_uses_numeric_target_node_id_and_skips_existing():
    client = FakeImportClient()
    client.relations.add((3001, 4444, "parent"))
    engine = ImportEngine(make_config(), client=client)
    workbook = MigrationWorkbook(
        xlsx_path=Path("migration.xlsx"),
        workspaces=[WorkspaceRow(row_index=4, location=r"MIGR\Client", title="DEV_TEST_001")],
        files=[],
        sheet_names=["Workspace", "File", "RelatedWorkspace"],
        related_workspaces=[
            RelatedWorkspaceRow(
                row_index=2,
                source_workspace="DEV_TEST_001",
                target_workspace="",
                target_node_id=4444,
                relation_type="parent",
                enabled=True,
            )
        ],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.related_existing == 1
    assert stats.related_created == 0
    assert client.relation_posts == []
    assert stats.related_rows[0].target_node_id == 4444
    assert stats.related_rows[0].status == "existing"


def test_import_engine_workspace_api_failure_increments_ws_failed():
    client = FakeFailingWorkspaceClient()
    engine = ImportEngine(make_config(), client=client)
    workbook = MigrationWorkbook(
        xlsx_path=Path("migration.xlsx"),
        workspaces=[
            WorkspaceRow(
                row_index=4,
                location=r"MIGR\Client",
                title="DEV_TEST_003",
                cat_values={},
            )
        ],
        files=[],
        sheet_names=["Workspace", "File"],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.ws_failed == 1
    assert stats.ws_created == 0
    assert "Workspace row 4 failed: Business Workspace creation failed for 'DEV_TEST_003' HTTP 500: failed" in (
        stats.details
    )
    assert stats.workspace_rows[0].row_index == 4
    assert stats.workspace_rows[0].excel_title == "DEV_TEST_003"
    assert stats.workspace_rows[0].status == "failed"
    assert stats.workspace_rows[0].error == "Business Workspace creation failed for 'DEV_TEST_003' HTTP 500: failed"


def test_import_engine_unresolved_dev_test_file_location_is_not_created_as_folder():
    client = FakeFailingWorkspaceClient()
    engine = ImportEngine(make_config(), client=client)
    workbook = MigrationWorkbook(
        xlsx_path=Path("migration.xlsx"),
        workspaces=[
            WorkspaceRow(
                row_index=4,
                location=r"MIGR\Client",
                title="DEV_TEST_003",
                cat_values={},
            )
        ],
        files=[
            FileRow(
                row_index=8,
                location=r"MIGR\Client\DEV_TEST_003\03_Other documents",
                title="example.txt",
                local_path=r"C:\files\example.txt",
            )
        ],
        sheet_names=["Workspace", "File"],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.ws_failed == 1
    assert stats.f_failed == 1
    assert client.resolved_paths == [r"MIGR\Client"]
    assert client.upload_parent_ids == []
    assert "File row 8 failed: Unresolved workspace placeholder in file location: DEV_TEST_003" in stats.details
    assert stats.file_rows[0].row_index == 8
    assert stats.file_rows[0].status == "failed"
    assert stats.file_rows[0].error == "Unresolved workspace placeholder in file location: DEV_TEST_003"


def test_import_engine_file_failed_audit_row():
    client = FakeFailingFileClient()
    engine = ImportEngine(make_config(), client=client)
    workbook = MigrationWorkbook(
        xlsx_path=Path("migration.xlsx"),
        workspaces=[
            WorkspaceRow(
                row_index=4,
                location=r"MIGR\Client",
                title="DEV_TEST_001",
                cat_values={},
            )
        ],
        files=[
            FileRow(
                row_index=9,
                location=r"MIGR\Client\DEV_TEST_001\03_Other documents",
                title="failed.txt",
                local_path=r"C:\files\failed.txt",
            )
        ],
        sheet_names=["Workspace", "File"],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.f_failed == 1
    assert stats.file_rows[0].row_index == 9
    assert stats.file_rows[0].title == "failed.txt"
    assert stats.file_rows[0].status == "failed"
    assert stats.file_rows[0].error == "Upload returned failed"


def test_execution_report_files_are_generated(tmp_path):
    client = FakeImportClient()
    cfg = make_config()
    workbook = MigrationWorkbook(
        xlsx_path=Path("migration.xlsx"),
        workspaces=[WorkspaceRow(row_index=4, location=r"MIGR\Client", title="DEV_TEST_001")],
        files=[
            FileRow(
                row_index=2,
                location=r"MIGR\Client\DEV_TEST_001\03_Other documents",
                title="example.txt",
                local_path=r"C:\files\example.txt",
            )
        ],
        sheet_names=["Workspace", "File"],
    )
    stats = ImportEngine(cfg, client=client).run(workbook, execute=True)
    report = ImportExecutionReport.create(cfg=cfg, workbook=workbook, mode="execute", stats=stats)

    paths = ReportGenerator(tmp_path).write_execution_all(report, stem="migration", timestamp="20260603_120000")

    assert set(paths) == {"json", "markdown", "csv", "xlsx"}
    assert paths["json"].name == "migration_execution_20260603_120000.json"
    assert paths["markdown"].name == "migration_execution_20260603_120000.md"
    assert paths["csv"].name == "migration_execution_20260603_120000.csv"
    assert paths["xlsx"].name == "migration_execution_20260603_120000.xlsx"
    for path in paths.values():
        assert path.exists()
