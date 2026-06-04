from contentflow_ai.migration.config import MigrationConfig
import pytest

from contentflow_ai.migration.cs_client import CSClient, CSClientError, NodeInfo


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

    def create_folder(self, parent_id: int, name: str) -> int:
        node_id = max(self.nodes) + 1
        node = NodeInfo(node_id, name, 0)
        self.nodes[node_id] = node
        self.children.setdefault(parent_id, {})[name] = node
        return node_id


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.text or f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.headers = {}

    def post(self, *args, **kwargs) -> FakeResponse:
        return self.response


class FakeBusinessWorkspaceClient(FakeCSClient):
    def __init__(self, cfg: MigrationConfig, response_payload: dict, status_code: int = 201, text: str = ""):
        super().__init__(cfg)
        self.session = FakeSession(FakeResponse(status_code, response_payload, text))
        self.nodes[3001] = NodeInfo(3001, "SPLIC - 00143", 848)
        self.created_folders = []

    def find_child(self, parent_id: int, name: str) -> NodeInfo | None:
        return None

    def create_folder(self, parent_id: int, name: str) -> int:
        self.created_folders.append((parent_id, name))
        return super().create_folder(parent_id, name)


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


def test_resolve_or_create_path_still_creates_intermediate_folders():
    client = FakeCSClient(make_config())

    node_id = client.resolve_or_create_path(r"MIGR\Client\New\Reports")

    assert node_id == 2004
    assert client.children[2001]["New"].node_id == 2003
    assert client.children[2003]["Reports"].node_id == 2004


def test_business_workspace_api_failure_does_not_call_create_folder():
    cfg = make_config()
    cfg.template_id = 111
    cfg.wksp_type_id = 222
    client = FakeBusinessWorkspaceClient(
        cfg,
        {"error": "bad request"},
        status_code=500,
        text="server generated workspace failed",
    )

    with pytest.raises(CSClientError) as exc_info:
        client.create_or_get_workspace(2001, "DEV_TEST_003", {})

    assert client.created_folders == []
    assert "DEV_TEST_003" in str(exc_info.value)
    assert "HTTP 500" in str(exc_info.value)
    assert "server generated workspace failed" in str(exc_info.value)


def test_business_workspace_mode_without_template_does_not_create_folder():
    client = FakeBusinessWorkspaceClient(make_config(), {"results": {"id": 3001}})

    with pytest.raises(CSClientError) as exc_info:
        client.create_or_get_workspace(2001, "DEV_TEST_003", {})

    assert client.created_folders == []
    assert "DEV_TEST_003" in str(exc_info.value)


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


class RelatedSession:
    def __init__(self, get_payload: dict | None = None):
        self.headers = {}
        self.get_payload = get_payload or {"results": []}
        self.post_calls = []
        self.get_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return FakeResponse(200, self.get_payload)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return FakeResponse(201, {"results": {"ok": True}})


def test_add_business_workspace_relation_posts_official_relateditems_form_data():
    client = CSClient(make_config())
    session = RelatedSession()
    client.session = session

    client.add_business_workspace_relation(3001, 3002, "parent")

    assert session.post_calls[0][0] == "https://example/otcs/cs.exe/api/v2/businessworkspaces/3001/relateditems"
    assert session.post_calls[0][1]["data"] == {"rel_bw_id": 3002, "rel_type": "parent"}


def test_relation_exists_checks_relateditems_before_duplicate_creation():
    client = CSClient(make_config())
    client.session = RelatedSession({"results": [{"rel_bw_id": 3002, "rel_type": "child"}]})

    assert client.relation_exists(3001, 3002, "child") is True
    assert client.relation_exists(3001, 3003, "child") is False


def test_search_business_workspaces_by_name_parses_mocked_expanded_response_and_uses_cache():
    client = CSClient(make_config())
    session = RelatedSession({
        "results": [
            {"data": {"properties": {"id": 3166, "name": "SPLIC - 00166", "type": 848}}},
            {"data": {"properties": {"id": 9999, "name": "SPLIC - 99999", "type": 848}}},
        ]
    })
    client.session = session

    first = client.search_business_workspaces_by_name("SPLIC - 00166")
    second = client.search_business_workspaces_by_name("SPLIC - 00166")

    assert [(item.node_id, item.name, item.type) for item in first] == [
        (3166, "SPLIC - 00166", 848),
        (9999, "SPLIC - 99999", 848),
    ]
    assert second == first
    assert len(session.get_calls) == 1
    assert session.get_calls[0][0] == "https://example/otcs/cs.exe/api/v2/businessworkspaces"
    assert session.get_calls[0][1]["params"] == {
        "expanded_view": "true",
        "where_name": "SPLIC - 00166",
        "limit": 10,
        "page": 1,
    }


def test_resolve_business_workspace_reference_uses_exact_single_name_match():
    client = CSClient(make_config())
    client.session = RelatedSession({
        "results": [
            {"data": {"properties": {"id": 1111, "name": "SPLIC - 00166 extra", "type": 848}}},
            {"data": {"properties": {"id": 3166, "name": "SPLIC - 00166", "type": 848}}},
        ]
    })

    resolved = client.resolve_business_workspace_reference("SPLIC - 00166", {}, {})

    assert resolved.node_id == 3166
    assert resolved.name == "SPLIC - 00166"
    assert resolved.status == "resolved"


def test_resolve_business_workspace_reference_reports_ambiguous_exact_matches():
    client = CSClient(make_config())
    client.session = RelatedSession({
        "results": [
            {"data": {"properties": {"id": 3166, "name": "SPLIC - 00166", "type": 848}}},
            {"data": {"properties": {"id": 4166, "name": "SPLIC - 00166", "type": 848}}},
        ]
    })

    resolved = client.resolve_business_workspace_reference("SPLIC - 00166", {}, {})

    assert resolved.node_id is None
    assert resolved.status == "ambiguous"
    assert resolved.error_code == "AMBIGUOUS"
