from pathlib import Path

from contentflow_ai.migration.config import MigrationConfig
from contentflow_ai.migration.cs_client import CSClientError
from contentflow_ai.migration.import_engine import ImportEngine
from contentflow_ai.migration.models import FileRow, MigrationWorkbook, WorkspaceRow


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
        self.resolved_paths = []
        self.upload_parent_ids = []

    def authenticate(self) -> None:
        return None

    def resolve_or_create_path(self, location: str) -> int:
        self.resolved_paths.append(location)
        return len(self.resolved_paths) + 100

    def create_or_get_workspace(self, parent_id: int, excel_name: str, cat_values: dict):
        self.ws_name_map[excel_name] = "SPLIC - 00143"
        return 3001, True, "SPLIC - 00143"

    def remap_file_location(self, location: str) -> str:
        for excel_name, actual_name in self.ws_name_map.items():
            location = location.replace(excel_name, actual_name)
        return location

    def upload_file(self, parent_id: int, name: str, local_path: str, mime_hint: str = "") -> str:
        self.upload_parent_ids.append(parent_id)
        return "uploaded"


class FakeFailingWorkspaceClient(FakeImportClient):
    def create_or_get_workspace(self, parent_id: int, excel_name: str, cat_values: dict):
        raise CSClientError(f"Business Workspace creation failed for '{excel_name}' HTTP 500: failed")


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
