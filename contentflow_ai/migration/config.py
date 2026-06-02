from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import CategoryFieldConfig

_ENV_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)(?::-(.*))?\}$")
SECRET_KEYS = {"password", "token", "secret", "api_key", "otcs_ticket"}


@dataclass(slots=True)
class MigrationConfig:
    base_url: str
    username: str
    password: str
    enterprise_node_id: int
    template_id: int | None
    wksp_type_id: int | None
    category_id: int | str
    ws_sheet: str
    file_sheet: str
    ws_columns: dict[str, int]
    file_columns: dict[str, int]
    category_fields: dict[str, CategoryFieldConfig]
    local_file_root: str = ""
    on_duplicate: str = "skip"
    request_delay: float = 0.3
    ssl_verify: bool = True
    dry_run: bool = True
    ws_data_start_row: int = 4
    file_data_start_row: int = 0
    project_name: str = "ContentFlow AI - Migration Copilot"
    raw: dict[str, Any] = field(default_factory=dict)

    def redacted(self) -> dict[str, Any]:
        redacted = dict(self.raw)
        for key in list(redacted.keys()):
            if any(secret in key.lower() for secret in SECRET_KEYS):
                redacted[key] = "***REDACTED***" if redacted.get(key) else ""
        return redacted


def load_config(config_path: str | Path, *, dry_run: bool | None = None) -> MigrationConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    resolved = _resolve_env_placeholders(data)

    defaults = {
        "local_file_root": "",
        "on_duplicate": "skip",
        "request_delay": 0.3,
        "ssl_verify": True,
        "dry_run": True,
        "ws_data_start_row": 4,
        "file_data_start_row": 0,
        "project_name": "ContentFlow AI - Migration Copilot",
    }
    for key, value in defaults.items():
        resolved.setdefault(key, value)
    if dry_run is not None:
        resolved["dry_run"] = dry_run

    required = [
        "base_url",
        "username",
        "password",
        "enterprise_node_id",
        "category_id",
        "ws_sheet",
        "file_sheet",
        "ws_columns",
        "file_columns",
        "category_fields",
    ]
    missing = [key for key in required if key not in resolved or resolved[key] in (None, "")]
    if missing:
        raise ValueError(f"Config missing required keys: {', '.join(missing)}")

    category_fields = {}
    for key, field_data in resolved.get("category_fields", {}).items():
        category_fields[key] = CategoryFieldConfig(
            key=key,
            attr_id=field_data["attr_id"],
            col=field_data.get("col"),
            col_start=field_data.get("col_start"),
            col_end=field_data.get("col_end"),
            multi_value=bool(field_data.get("multi_value", False)),
            required=bool(field_data.get("required", False)),
            value_map=dict(field_data.get("value_map", {})),
            comment=field_data.get("_comment", ""),
        )

    return MigrationConfig(
        base_url=str(resolved["base_url"]).rstrip("/"),
        username=str(resolved["username"]),
        password=str(resolved["password"]),
        enterprise_node_id=int(resolved["enterprise_node_id"]),
        template_id=_optional_int(resolved.get("template_id")),
        wksp_type_id=_optional_int(resolved.get("wksp_type_id")),
        category_id=resolved["category_id"],
        ws_sheet=str(resolved["ws_sheet"]),
        file_sheet=str(resolved["file_sheet"]),
        ws_columns={k: int(v) for k, v in resolved["ws_columns"].items()},
        file_columns={k: int(v) for k, v in resolved["file_columns"].items()},
        category_fields=category_fields,
        local_file_root=str(resolved.get("local_file_root", "")),
        on_duplicate=str(resolved.get("on_duplicate", "skip")),
        request_delay=float(resolved.get("request_delay", 0.3)),
        ssl_verify=bool(resolved.get("ssl_verify", True)),
        dry_run=bool(resolved.get("dry_run", True)),
        ws_data_start_row=int(resolved.get("ws_data_start_row", 4)),
        file_data_start_row=int(resolved.get("file_data_start_row", 0)),
        project_name=str(resolved.get("project_name", "ContentFlow AI - Migration Copilot")),
        raw=resolved,
    )


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _resolve_env_placeholders(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _resolve_env_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_placeholders(item) for item in value]
    if isinstance(value, str):
        match = _ENV_PATTERN.match(value.strip())
        if match:
            env_name, default = match.groups()
            return os.getenv(env_name, default or "")
    return value
