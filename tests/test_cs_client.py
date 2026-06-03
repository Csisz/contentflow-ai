from contentflow_ai.migration.config import MigrationConfig
from contentflow_ai.migration.cs_client import CSClient, NodeInfo


def make_config() -> MigrationConfig:
    return MigrationConfig(
        base_url="https://example/otcs/cs.exe",
        username="technical_user",
        password="secret",
        enterprise_node_id=2000,
        template_id=None,
        wksp_type_id=None,
        category_id=123,
        ws_sheet="Workspace",
        file_sheet="File",
        ws_columns={"location": 0, "title": 1},
        file_columns={"location": 0, "title": 1, "src": 2},
        category_fields={},
    )


class FakeCSClient(CSClient):
    def __init__(self, cfg: MigrationConfig):
        super().__init__(cfg)
        self.nodes = {
            2000: NodeInfo(2000, "MIGR", 0),
            2001: NodeInfo(2001, "Client", 0),
            2002: NodeInfo(2002, "Existing", 0),
        }
        self.children = {
            2000: {"Client": self.nodes[2001]},
            2001: {"Existing": self.nodes[2002]},
        }

    def get_node(self, node_id: int) -> NodeInfo | None:
        return self.nodes.get(node_id)

    def find_child(self, parent_id: int, name: str) -> NodeInfo | None:
        return self.children.get(parent_id, {}).get(name)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.headers = {}

    def post(self, *args, **kwargs) -> FakeResponse:
        return self.response


class FakeBusinessWorkspaceClient(FakeCSClient):
    def __init__(self, cfg: MigrationConfig, response_payload: dict):
        super().__init__(cfg)
        self.session = FakeSession(FakeResponse(201, response_payload))
        self.nodes[3001] = NodeInfo(3001, "SPLIC - 00143", 848)

    def find_child(self, parent_id: int, name: str) -> NodeInfo | None:
        return None


def test_plan_path_creation_skips_enterprise_prefix():
    client = FakeCSClient(make_config())

    plan = client.plan_path_creation("Enterprise/MIGR/Client")

    assert plan.root_node_id == 2000
    assert plan.root_name == "MIGR"
    assert plan.relative_path == "Client"
    assert plan.existing_until_node_id == 2001
    assert plan.full_path_exists is True
    assert plan.action == "exists"


def test_plan_path_creation_skips_root_node_name_prefix():
    client = FakeCSClient(make_config())

    plan = client.plan_path_creation("MIGR/Client")

    assert plan.root_node_id == 2000
    assert plan.relative_path == "Client"
    assert plan.existing_until_path == "MIGR/Client"
    assert plan.full_path_exists is True


def test_plan_path_creation_existing_path_returns_full_path_exists():
    client = FakeCSClient(make_config())

    plan = client.plan_path_creation(r"Enterprise Workspace\MIGR\Client\Existing")

    assert plan.root_node_id == 2000
    assert plan.relative_path == "Client/Existing"
    assert plan.existing_until_node_id == 2002
    assert plan.missing_parts == []
    assert plan.full_path_exists is True


def test_plan_path_creation_missing_tail_is_planned_under_configured_root():
    client = FakeCSClient(make_config())

    plan = client.plan_path_creation("Enterprise/MIGR/Client/New/Reports")

    assert plan.root_node_id == 2000
    assert plan.existing_until_node_id == 2001
    assert plan.existing_until_path == "MIGR/Client"
    assert plan.missing_parts == ["New", "Reports"]
    assert plan.full_path_exists is False
    assert plan.action == "create_missing_folders"


def test_create_business_workspace_fetches_actual_node_name_when_response_returns_id_only():
    cfg = make_config()
    cfg.template_id = 111
    cfg.wksp_type_id = 222
    client = FakeBusinessWorkspaceClient(cfg, {"results": {"id": 3001}})

    node_id, created, actual_name = client.create_or_get_workspace(2001, "DEV_TEST_001", {})

    assert node_id == 3001
    assert created is True
    assert actual_name == "SPLIC - 00143"
    assert client.ws_name_map["DEV_TEST_001"] == "SPLIC - 00143"
    assert client.remap_file_location(
        r"Enterprise\Egis HU\Operations\Printed Packaging Material Specifications Licence-in (SP)"
        r"\DEV_TEST_001\03_Other documents"
    ) == (
        r"Enterprise\Egis HU\Operations\Printed Packaging Material Specifications Licence-in (SP)"
        r"\SPLIC - 00143\03_Other documents"
    )


def test_create_business_workspace_prefers_fetched_node_name_over_placeholder_response_name():
    cfg = make_config()
    cfg.template_id = 111
    cfg.wksp_type_id = 222
    client = FakeBusinessWorkspaceClient(
        cfg,
        {"results": {"id": 3001, "data": {"properties": {"name": "DEV_TEST_001"}}}},
    )

    _node_id, _created, actual_name = client.create_or_get_workspace(2001, "DEV_TEST_001", {})

    assert actual_name == "SPLIC - 00143"
    assert client.ws_name_map["DEV_TEST_001"] == "SPLIC - 00143"
