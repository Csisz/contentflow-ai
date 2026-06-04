from pathlib import Path

from contentflow_ai.migration.config import MigrationConfig
from contentflow_ai.migration.cs_client import CSClientError, ResolvedWorkspaceRef
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
        self.existing_workspace_matches = {}
        self.search_calls = []
        self._existing_workspace_cache = {}

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

    def resolve_business_workspace_reference(self, ref: str, workspace_id_map: dict, workspace_name_map: dict):
        value = str(ref or "").strip()
        if not value:
            return ResolvedWorkspaceRef(None, status="missing", error_code="NOT_FOUND")
        if value.isdigit():
            return ResolvedWorkspaceRef(int(value), value, "resolved")
        if value in workspace_id_map:
            return ResolvedWorkspaceRef(int(workspace_id_map[value]), value, "resolved")
        actual_name = workspace_name_map.get(value)
        if actual_name and actual_name in workspace_id_map:
            return ResolvedWorkspaceRef(int(workspace_id_map[actual_name]), actual_name, "resolved")
        if value in self._existing_workspace_cache:
            matches = self._existing_workspace_cache[value]
        else:
            self.search_calls.append(value)
            matches = self.existing_workspace_matches.get(value, [])
            self._existing_workspace_cache[value] = matches
        exact_matches = [match for match in matches if match[1].strip() == value]
        if not exact_matches:
            return ResolvedWorkspaceRef(None, value, "missing", "NOT_FOUND")
        if len(exact_matches) > 1:
            return ResolvedWorkspaceRef(None, value, "ambiguous", "AMBIGUOUS")
        node_id, name = exact_matches[0]
        return ResolvedWorkspaceRef(node_id, name, "resolved")


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


def test_import_engine_related_target_node_id_wins_over_target_workspace_name():
    client = FakeImportClient()
    client.existing_workspace_matches["SPLIC - 99999"] = [(9999, "SPLIC - 99999")]
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
                target_workspace="SPLIC - 99999",
                target_node_id=4444,
                enabled=True,
            )
        ],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.related_created == 1
    assert client.relation_posts == [(3001, 4444, "child")]
    assert client.search_calls == []


def test_import_engine_related_target_workspace_numeric_string_resolves_as_node_id():
    client = FakeImportClient()
    engine = ImportEngine(make_config(), client=client)
    workbook = MigrationWorkbook(
        xlsx_path=Path("migration.xlsx"),
        workspaces=[WorkspaceRow(row_index=4, location=r"MIGR\Client", title="DEV_TEST_001")],
        files=[],
        sheet_names=["Workspace", "File", "RelatedWorkspace"],
        related_workspaces=[
            RelatedWorkspaceRow(row_index=2, source_workspace="DEV_TEST_001", target_workspace="4444", enabled=True)
        ],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.related_created == 1
    assert client.relation_posts == [(3001, 4444, "child")]
    assert stats.related_rows[0].target_resolved_name == "4444"


def test_import_engine_related_target_generated_name_resolves_from_current_import_mapping():
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
                target_workspace="SPLIC - 00144",
                enabled=True,
            )
        ],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.related_created == 1
    assert client.relation_posts == [(3001, 3002, "child")]
    assert client.search_calls == []


def test_import_engine_related_target_existing_name_resolves_via_search_and_creates_relation():
    client = FakeImportClient()
    client.existing_workspace_matches["SPLIC - 00166"] = [(3166, "SPLIC - 00166")]
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
                target_workspace="SPLIC - 00166",
                enabled=True,
            )
        ],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.related_created == 1
    assert client.relation_posts == [(3001, 3166, "child")]
    assert stats.related_rows[0].target_resolved_name == "SPLIC - 00166"


def test_import_engine_related_target_zero_matches_fails_not_found():
    client = FakeImportClient()
    engine = ImportEngine(make_config(), client=client)
    workbook = MigrationWorkbook(
        xlsx_path=Path("migration.xlsx"),
        workspaces=[WorkspaceRow(row_index=4, location=r"MIGR\Client", title="DEV_TEST_001")],
        files=[],
        sheet_names=["Workspace", "File", "RelatedWorkspace"],
        related_workspaces=[
            RelatedWorkspaceRow(row_index=2, source_workspace="DEV_TEST_001", target_workspace="SPLIC - 00166", enabled=True)
        ],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.related_failed == 1
    assert stats.related_rows[0].status == "failed"
    assert stats.related_rows[0].error_message == "RELATED_TARGET_NOT_FOUND"


def test_import_engine_related_target_multiple_exact_matches_fails_ambiguous():
    client = FakeImportClient()
    client.existing_workspace_matches["SPLIC - 00166"] = [(3166, "SPLIC - 00166"), (4166, "SPLIC - 00166")]
    engine = ImportEngine(make_config(), client=client)
    workbook = MigrationWorkbook(
        xlsx_path=Path("migration.xlsx"),
        workspaces=[WorkspaceRow(row_index=4, location=r"MIGR\Client", title="DEV_TEST_001")],
        files=[],
        sheet_names=["Workspace", "File", "RelatedWorkspace"],
        related_workspaces=[
            RelatedWorkspaceRow(row_index=2, source_workspace="DEV_TEST_001", target_workspace="SPLIC - 00166", enabled=True)
        ],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.related_failed == 1
    assert stats.related_rows[0].error_message == "RELATED_TARGET_AMBIGUOUS"


def test_import_engine_related_source_existing_name_resolves_via_search():
    client = FakeImportClient()
    client.existing_workspace_matches["SPLIC - 00165"] = [(3165, "SPLIC - 00165")]
    client.existing_workspace_matches["SPLIC - 00166"] = [(3166, "SPLIC - 00166")]
    engine = ImportEngine(make_config(), client=client)
    workbook = MigrationWorkbook(
        xlsx_path=Path("migration.xlsx"),
        workspaces=[],
        files=[],
        sheet_names=["Workspace", "File", "RelatedWorkspace"],
        related_workspaces=[
            RelatedWorkspaceRow(
                row_index=2,
                source_workspace="SPLIC - 00165",
                target_workspace="SPLIC - 00166",
                enabled=True,
            )
        ],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.related_created == 1
    assert client.relation_posts == [(3165, 3166, "child")]
    assert stats.related_rows[0].source_resolved_name == "SPLIC - 00165"


def test_import_engine_related_source_zero_matches_fails_not_found():
    client = FakeImportClient()
    client.existing_workspace_matches["SPLIC - 00166"] = [(3166, "SPLIC - 00166")]
    engine = ImportEngine(make_config(), client=client)
    workbook = MigrationWorkbook(
        xlsx_path=Path("migration.xlsx"),
        workspaces=[],
        files=[],
        sheet_names=["Workspace", "File", "RelatedWorkspace"],
        related_workspaces=[
            RelatedWorkspaceRow(row_index=2, source_workspace="MISSING", target_workspace="SPLIC - 00166", enabled=True)
        ],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.related_failed == 1
    assert stats.related_rows[0].error_message == "RELATED_SOURCE_NOT_FOUND"


def test_import_engine_related_source_multiple_exact_matches_fails_ambiguous():
    client = FakeImportClient()
    client.existing_workspace_matches["SPLIC - 00165"] = [(3165, "SPLIC - 00165"), (4165, "SPLIC - 00165")]
    client.existing_workspace_matches["SPLIC - 00166"] = [(3166, "SPLIC - 00166")]
    engine = ImportEngine(make_config(), client=client)
    workbook = MigrationWorkbook(
        xlsx_path=Path("migration.xlsx"),
        workspaces=[],
        files=[],
        sheet_names=["Workspace", "File", "RelatedWorkspace"],
        related_workspaces=[
            RelatedWorkspaceRow(
                row_index=2,
                source_workspace="SPLIC - 00165",
                target_workspace="SPLIC - 00166",
                enabled=True,
            )
        ],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.related_failed == 1
    assert stats.related_rows[0].error_message == "RELATED_SOURCE_AMBIGUOUS"


def test_import_engine_repeated_same_target_name_uses_cached_search_result():
    client = FakeImportClient()
    client.existing_workspace_matches["SPLIC - 00166"] = [(3166, "SPLIC - 00166")]
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
            RelatedWorkspaceRow(row_index=2, source_workspace="DEV_TEST_001", target_workspace="SPLIC - 00166", enabled=True),
            RelatedWorkspaceRow(row_index=3, source_workspace="DEV_TEST_002", target_workspace="SPLIC - 00166", enabled=True),
        ],
    )

    stats = engine.run(workbook, execute=True)

    assert stats.related_created == 2
    assert client.search_calls == ["SPLIC - 00166"]


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
