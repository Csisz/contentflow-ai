from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .config import MigrationConfig
from .utils import get_mime, normalize_ws_name


@dataclass(slots=True)
class NodeInfo:
    node_id: int
    name: str
    type: int | None = None


@dataclass(slots=True)
class PathCreationPlan:
    requested_path: str
    relative_path: str
    root_node_id: int
    root_name: str
    existing_until_node_id: int
    existing_until_path: str
    missing_parts: list[str]
    full_path_exists: bool
    action: str


class CSClientError(RuntimeError):
    pass


class CSClient:
    """Small OpenText Content Server REST client.

    The class contains both read-only helpers for preflight and write methods for
    the later execute mode. Preflight should only call authenticate(), get_node(),
    find_child(), resolve_existing_path() and plan_path_creation().
    """

    def __init__(self, cfg: MigrationConfig):
        self.cfg = cfg
        self.base = cfg.base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._cache: dict[str, int] = {}
        self.ws_name_map: dict[str, str] = {}

    def authenticate(self) -> None:
        response = self.session.post(
            f"{self.base}/api/v1/auth",
            verify=self.cfg.ssl_verify,
            data={"username": self.cfg.username, "password": self.cfg.password},
            timeout=60,
        )
        if response.status_code != 200:
            raise CSClientError(f"Authentication failed HTTP {response.status_code}: {response.text[:200]}")
        ticket = response.json().get("ticket")
        if not ticket:
            raise CSClientError("Authentication succeeded but no OTCSTicket was returned")
        self.session.headers["OTCSTicket"] = ticket

    def get_node(self, node_id: int) -> NodeInfo | None:
        try:
            data = self._get(f"/api/v2/nodes/{node_id}")
            props = data["results"]["data"]["properties"]
            return NodeInfo(node_id=int(props["id"]), name=props.get("name", ""), type=props.get("type"))
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

    def find_child(self, parent_id: int, name: str) -> NodeInfo | None:
        data = self._get(f"/api/v2/nodes/{parent_id}/nodes", params={"where_name": name, "limit": 10})
        for item in data.get("results", []):
            props = item.get("data", {}).get("properties", {})
            if props.get("name") == name:
                return NodeInfo(node_id=int(props["id"]), name=props.get("name", ""), type=props.get("type"))
        return None

    def resolve_existing_path(self, cs_path: str) -> int | None:
        """Resolve an existing CS path without creating missing folders."""
        if cs_path in self._cache:
            return self._cache[cs_path]
        parts = [part for part in cs_path.replace("\\", "/").split("/") if part]
        if not parts:
            return self.cfg.enterprise_node_id
        current_id = self.cfg.enterprise_node_id
        built = parts[0]
        self._cache[built] = current_id
        for part in parts[1:]:
            key = f"{built}/{part}"
            if key in self._cache:
                current_id = self._cache[key]
                built = key
                continue
            child = self.find_child(current_id, part)
            if child is None:
                return None
            current_id = child.node_id
            self._cache[key] = current_id
            built = key
        self._cache[cs_path] = current_id
        return current_id

    def plan_path_creation(self, cs_path: str) -> PathCreationPlan:
        """Plan read-only path resolution under the configured migration root."""
        root_id = self.cfg.enterprise_node_id
        root = self.get_node(root_id)
        if root is None:
            raise CSClientError(f"Configured enterprise_node_id was not found: {root_id}")

        parts = [part.strip() for part in cs_path.replace("\\", "/").split("/") if part.strip()]
        relative_parts = self._strip_root_prefixes(parts, root.name)
        relative_path = "/".join(relative_parts)

        current_id = root_id
        existing_parts: list[str] = []
        missing_parts: list[str] = []
        for part in relative_parts:
            if missing_parts:
                missing_parts.append(part)
                continue

            child = self.find_child(current_id, part)
            if child is None:
                missing_parts.append(part)
                continue

            current_id = child.node_id
            existing_parts.append(child.name or part)

        full_path_exists = not missing_parts
        existing_until_path = "/".join([root.name, *existing_parts]) if root.name else "/".join(existing_parts)
        return PathCreationPlan(
            requested_path=cs_path,
            relative_path=relative_path,
            root_node_id=root_id,
            root_name=root.name,
            existing_until_node_id=current_id,
            existing_until_path=existing_until_path,
            missing_parts=missing_parts,
            full_path_exists=full_path_exists,
            action="exists" if full_path_exists else "create_missing_folders",
        )

    def resolve_or_create_path(self, cs_path: str) -> int:
        """Resolve a path and create missing folders. Use only in execute mode."""
        parts = [part for part in cs_path.replace("\\", "/").split("/") if part]
        if not parts:
            return self.cfg.enterprise_node_id
        current_id = self.cfg.enterprise_node_id
        built = parts[0]
        self._cache[built] = current_id
        for part in parts[1:]:
            key = f"{built}/{part}"
            if key in self._cache:
                current_id = self._cache[key]
                built = key
                continue
            child = self.find_child(current_id, part)
            if child is None:
                child_id = self.create_folder(current_id, part)
            else:
                child_id = child.node_id
            self._cache[key] = child_id
            current_id = child_id
            built = key
        self._cache[cs_path] = current_id
        return current_id

    def create_folder(self, parent_id: int, name: str) -> int:
        data = self._post("/api/v2/nodes", data={"type": 0, "parent_id": parent_id, "name": name})
        return int(data["results"]["data"]["properties"]["id"])

    def create_or_get_workspace(self, parent_id: int, excel_name: str, cat_values: dict[str, Any]) -> tuple[int, bool, str]:
        existing = self.find_child(parent_id, excel_name)
        if existing:
            self._register_name(excel_name, existing.name)
            return existing.node_id, False, existing.name

        if self.cfg.template_id is None or self.cfg.wksp_type_id is None:
            node_id = self.create_folder(parent_id, excel_name)
            self._register_name(excel_name, excel_name)
            return node_id, True, excel_name

        body = {
            "type": 848,
            "parent_id": parent_id,
            "name": excel_name,
            "template_id": self.cfg.template_id,
            "wksp_type_id": self.cfg.wksp_type_id,
            "roles": {"categories": {str(self.cfg.category_id): self._build_category_data(cat_values)}},
        }
        response = self.session.post(
            f"{self.base}/api/v2/businessworkspaces",
            verify=self.cfg.ssl_verify,
            data={"body": json.dumps(body)},
            timeout=120,
        )
        if response.status_code not in (200, 201):
            # Keep legacy-safe fallback, but callers should log this as warning.
            node_id = self.create_folder(parent_id, excel_name)
            self._register_name(excel_name, excel_name)
            return node_id, True, excel_name
        result = response.json()["results"]
        node_id = int(result["id"])
        actual_name = self._fetch_node_name(node_id)
        if actual_name is None:
            actual_name = result.get("data", {}).get("properties", {}).get("name") or excel_name
        self._register_name(excel_name, actual_name)
        return node_id, True, actual_name

    def apply_category(self, node_id: int, cat_values: dict[str, Any]) -> bool:
        body = {"categories": self._build_category_data(cat_values)}
        response = self.session.put(
            f"{self.base}/api/v2/nodes/{node_id}/categories/{self.cfg.category_id}",
            verify=self.cfg.ssl_verify,
            data={"body": json.dumps(body)},
            timeout=120,
        )
        return response.status_code in (200, 201)

    def upload_file(self, parent_id: int, name: str, local_path: str, mime_hint: str = "") -> str:
        if not Path(local_path).is_file():
            return "failed"
        existing = self.find_child(parent_id, name)
        mime = get_mime(local_path, mime_hint)
        if existing:
            if self.cfg.on_duplicate == "skip":
                return "skipped"
            if self.cfg.on_duplicate == "new_version":
                return self.add_version(existing.node_id, local_path, name, mime)
        with open(local_path, "rb") as handle:
            response = self.session.post(
                f"{self.base}/api/v2/nodes",
                verify=self.cfg.ssl_verify,
                data={"type": 144, "parent_id": parent_id, "name": name},
                files={"file": (name, handle, mime)},
                timeout=300,
            )
        return "uploaded" if response.status_code in (200, 201) else "failed"

    def add_version(self, node_id: int, local_path: str, name: str, mime: str) -> str:
        with open(local_path, "rb") as handle:
            response = self.session.post(
                f"{self.base}/api/v2/nodes/{node_id}/versions",
                verify=self.cfg.ssl_verify,
                files={"file": (name, handle, mime)},
                timeout=300,
            )
        return "versioned" if response.status_code in (200, 201) else "failed"

    def remap_file_location(self, location: str) -> str:
        if not self.ws_name_map:
            return location
        parts = location.replace("\\", "/").split("/")
        remapped = []
        for part in parts:
            norm = normalize_ws_name(part)
            remapped.append(self.ws_name_map.get(norm) or self.ws_name_map.get(part) or part)
        return "\\".join(remapped)

    def _build_category_data(self, cat_values: dict[str, Any]) -> dict[str, Any]:
        result = {}
        for key, field_cfg in self.cfg.category_fields.items():
            cs_key = f"{self.cfg.category_id}_{field_cfg.attr_id}"
            value = cat_values.get(key, [] if field_cfg.multi_value else "")
            result[cs_key] = value if value else ([""] if field_cfg.multi_value else "")
        return result

    def _register_name(self, excel_name: str, actual_name: str) -> None:
        self.ws_name_map[normalize_ws_name(excel_name)] = actual_name
        self.ws_name_map[excel_name] = actual_name

    def _fetch_node_name(self, node_id: int) -> str | None:
        node = self.get_node(node_id)
        if node and node.name:
            return node.name
        return None

    @staticmethod
    def _strip_root_prefixes(parts: list[str], root_name: str) -> list[str]:
        root_prefixes = {"enterprise", "enterprise workspace"}
        if root_name:
            root_prefixes.add(root_name.casefold())
        remaining = list(parts)
        while remaining and remaining[0].casefold() in root_prefixes:
            remaining.pop(0)
        return remaining

    def _get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        time.sleep(self.cfg.request_delay)
        response = self.session.get(f"{self.base}{path}", verify=self.cfg.ssl_verify, timeout=90, **kwargs)
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        time.sleep(self.cfg.request_delay)
        response = self.session.post(f"{self.base}{path}", verify=self.cfg.ssl_verify, timeout=120, **kwargs)
        response.raise_for_status()
        return response.json()
