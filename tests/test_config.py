import json

from contentflow_ai.migration.config import load_config


def test_load_config_resolves_env_placeholder(tmp_path, monkeypatch):
    monkeypatch.delenv("OTCS_BASE_URL", raising=False)
    monkeypatch.delenv("OTCS_USERNAME", raising=False)
    monkeypatch.delenv("OTCS_PASSWORD", raising=False)
    monkeypatch.setenv("OTCS_PASSWORD", "secret-from-env")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "base_url": "${OTCS_BASE_URL:-https://example/otcs/cs.exe}",
        "username": "${OTCS_USERNAME:-technical_user}",
        "password": "${OTCS_PASSWORD}",
        "enterprise_node_id": 2000,
        "category_id": 123,
        "ws_sheet": "Workspace",
        "file_sheet": "File",
        "ws_columns": {"location": 0, "title": 1},
        "file_columns": {"location": 0, "title": 1, "src": 2},
        "category_fields": {"doctype": {"attr_id": 3, "col": 4, "required": True}}
    }), encoding="utf-8")

    cfg = load_config(cfg_path)

    assert cfg.base_url == "https://example/otcs/cs.exe"
    assert cfg.password == "secret-from-env"
    assert cfg.category_fields["doctype"].required is True
    assert cfg.redacted()["password"] == "***REDACTED***"
