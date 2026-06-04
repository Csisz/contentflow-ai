from __future__ import annotations

import re
from dataclasses import asdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import MigrationConfig
from .cs_client import CSClient
from .models import MigrationWorkbook

_DEV_TEST_PATTERN = re.compile(r"^DEV_TEST_", re.IGNORECASE)


@dataclass(slots=True)
class WorkspaceExecutionRow:
    row_index: int
    excel_title: str
    workspace_name: str = ""
    node_id: int | None = None
    status: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class FileExecutionRow:
    row_index: int
    title: str
    source_path: str
    original_target_location: str
    remapped_target_location: str = ""
    parent_node_id: int | None = None
    status: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class RelatedWorkspaceExecutionRow:
    row_index: int
    source_workspace: str
    source_node_id: int | None = None
    target_workspace: str = ""
    target_node_id: int | None = None
    relation_type: str = "child"
    status: str = ""
    error_message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class ImportStats:
    ws_created: int = 0
    ws_existing: int = 0
    ws_failed: int = 0
    f_uploaded: int = 0
    f_versioned: int = 0
    f_skipped: int = 0
    f_failed: int = 0
    related_created: int = 0
    related_existing: int = 0
    related_failed: int = 0
    related_skipped: int = 0
    details: list[str] = field(default_factory=list)
    workspace_rows: list[WorkspaceExecutionRow] = field(default_factory=list)
    file_rows: list[FileExecutionRow] = field(default_factory=list)
    related_rows: list[RelatedWorkspaceExecutionRow] = field(default_factory=list)


@dataclass(slots=True)
class ImportExecutionReport:
    project_name: str
    generated_at: str
    xlsx_path: str
    mode: str
    stats: ImportStats

    @classmethod
    def create(
        cls,
        *,
        cfg: MigrationConfig,
        workbook: MigrationWorkbook,
        mode: str,
        stats: ImportStats,
    ) -> "ImportExecutionReport":
        return cls(
            project_name=cfg.project_name,
            generated_at=datetime.now(timezone.utc).isoformat(),
            xlsx_path=str(workbook.xlsx_path),
            mode=mode,
            stats=stats,
        )

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "generated_at": self.generated_at,
            "xlsx_path": self.xlsx_path,
            "mode": self.mode,
            "summary": {
                "ws_created": self.stats.ws_created,
                "ws_existing": self.stats.ws_existing,
                "ws_failed": self.stats.ws_failed,
                "f_uploaded": self.stats.f_uploaded,
                "f_versioned": self.stats.f_versioned,
                "f_skipped": self.stats.f_skipped,
                "f_failed": self.stats.f_failed,
                "related_created": self.stats.related_created,
                "related_existing": self.stats.related_existing,
                "related_failed": self.stats.related_failed,
                "related_skipped": self.stats.related_skipped,
            },
            "details": self.stats.details,
            "workspaces": [row.to_dict() for row in self.stats.workspace_rows],
            "related_workspaces": [row.to_dict() for row in self.stats.related_rows],
            "files": [row.to_dict() for row in self.stats.file_rows],
        }


class ImportEngine:
    """Execute or dry-run a migration workbook.

    This is intentionally separated from preflight. New development should call
    PreflightValidator first and only use ImportEngine after explicit approval.
    """

    def __init__(self, cfg: MigrationConfig, client: CSClient | None = None):
        self.cfg = cfg
        self.client = client or CSClient(cfg)

    def run(self, workbook: MigrationWorkbook, *, execute: bool = False) -> ImportStats:
        stats = ImportStats()
        if not execute or self.cfg.dry_run:
            stats.details.append("Dry-run mode: no Content Server write operations were executed.")
            stats.ws_created = len(workbook.workspaces)
            stats.f_uploaded = len(workbook.files)
            for ws in workbook.workspaces:
                stats.workspace_rows.append(
                    WorkspaceExecutionRow(
                        row_index=ws.row_index,
                        excel_title=ws.title,
                        workspace_name=ws.title,
                        status="planned",
                    )
                )
            for related in workbook.related_workspaces:
                relation_type = related.relation_type or "child"
                if not related.enabled:
                    stats.related_skipped += 1
                    status = "skipped"
                else:
                    stats.related_created += 1
                    status = "planned"
                stats.related_rows.append(
                    RelatedWorkspaceExecutionRow(
                        row_index=related.row_index,
                        source_workspace=related.source_workspace,
                        target_workspace=related.target_workspace,
                        target_node_id=related.target_node_id,
                        relation_type=relation_type,
                        status=status,
                    )
                )
            for file in workbook.files:
                stats.file_rows.append(
                    FileExecutionRow(
                        row_index=file.row_index,
                        title=file.title,
                        source_path=file.local_path,
                        original_target_location=file.location,
                        remapped_target_location=file.location,
                        status="planned",
                    )
                )
            return stats

        self.client.authenticate()
        failed_workspace_titles: set[str] = set()
        for ws in workbook.workspaces:
            audit_row = WorkspaceExecutionRow(row_index=ws.row_index, excel_title=ws.title)
            try:
                parent_id = self.client.resolve_or_create_path(ws.location)
                node_id, created, actual_name = self.client.create_or_get_workspace(parent_id, ws.title, ws.cat_values)
                audit_row.node_id = node_id
                audit_row.workspace_name = actual_name
                audit_row.status = "created" if created else "existing"
                if created:
                    stats.ws_created += 1
                else:
                    stats.ws_existing += 1
                if actual_name != ws.title:
                    stats.details.append(f"Workspace name remapped: {ws.title} -> {actual_name}")
            except Exception as exc:  # noqa: BLE001 - migration batch should continue and collect errors
                stats.ws_failed += 1
                failed_workspace_titles.add(ws.title)
                audit_row.status = "failed"
                audit_row.error = str(exc)
                stats.details.append(f"Workspace row {ws.row_index} failed: {exc}")
            finally:
                stats.workspace_rows.append(audit_row)

        self._process_related_workspaces(workbook, stats)

        for file in workbook.files:
            audit_row = FileExecutionRow(
                row_index=file.row_index,
                title=file.title,
                source_path=file.local_path,
                original_target_location=file.location,
            )
            try:
                original_location = file.location
                location = self.client.remap_file_location(file.location)
                audit_row.remapped_target_location = location
                if location != original_location:
                    stats.details.append(f"File location remapped: {original_location} -> {location}")
                unresolved = _find_unresolved_workspace_placeholder(location, failed_workspace_titles)
                if unresolved:
                    raise RuntimeError(f"Unresolved workspace placeholder in file location: {unresolved}")
                parent_id = self.client.resolve_or_create_path(location)
                audit_row.parent_node_id = parent_id
                result = self.client.upload_file(parent_id, file.title, file.local_path, file.mime_hint)
                audit_row.status = result
                if result == "uploaded":
                    stats.f_uploaded += 1
                elif result == "versioned":
                    stats.f_versioned += 1
                elif result == "skipped":
                    stats.f_skipped += 1
                else:
                    stats.f_failed += 1
                    audit_row.status = "failed"
                    audit_row.error = "Upload returned failed"
            except Exception as exc:  # noqa: BLE001
                stats.f_failed += 1
                audit_row.status = "failed"
                audit_row.error = str(exc)
                stats.details.append(f"File row {file.row_index} failed: {exc}")
            finally:
                stats.file_rows.append(audit_row)
        return stats

    def _process_related_workspaces(self, workbook: MigrationWorkbook, stats: ImportStats) -> None:
        for related in workbook.related_workspaces:
            relation_type = related.relation_type or "child"
            audit_row = RelatedWorkspaceExecutionRow(
                row_index=related.row_index,
                source_workspace=related.source_workspace,
                target_workspace=related.target_workspace,
                target_node_id=related.target_node_id,
                relation_type=relation_type,
            )
            if not related.enabled:
                stats.related_skipped += 1
                audit_row.status = "skipped"
                audit_row.error_message = "Relation row is disabled."
                stats.related_rows.append(audit_row)
                continue
            try:
                source_node_id = _resolve_workspace_ref(self.client, related.source_workspace)
                target_node_id = related.target_node_id or _resolve_workspace_ref(self.client, related.target_workspace)
                audit_row.source_node_id = source_node_id
                audit_row.target_node_id = target_node_id
                if source_node_id is None:
                    raise RuntimeError("RELATED_SOURCE_NOT_FOUND")
                if target_node_id is None:
                    raise RuntimeError("RELATED_TARGET_NOT_FOUND")
                if self.client.relation_exists(source_node_id, target_node_id, relation_type):
                    stats.related_existing += 1
                    audit_row.status = "existing"
                else:
                    self.client.add_business_workspace_relation(source_node_id, target_node_id, relation_type)
                    stats.related_created += 1
                    audit_row.status = "created"
            except Exception as exc:  # noqa: BLE001
                stats.related_failed += 1
                audit_row.status = "failed"
                audit_row.error_message = str(exc)
                stats.details.append(f"RelatedWorkspace row {related.row_index} failed: {exc}")
            finally:
                stats.related_rows.append(audit_row)


def _find_unresolved_workspace_placeholder(location: str, failed_workspace_titles: set[str]) -> str | None:
    parts = [part for part in location.replace("\\", "/").split("/") if part]
    for part in parts:
        if part in failed_workspace_titles or _DEV_TEST_PATTERN.match(part):
            return part
    return None


def _resolve_workspace_ref(client: CSClient, value: str) -> int | None:
    resolver = getattr(client, "resolve_workspace_node_id", None)
    if callable(resolver):
        return resolver(value)
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    node_map = getattr(client, "ws_node_id_map", {})
    if value in node_map:
        return int(node_map[value])
    name_map = getattr(client, "ws_name_map", {})
    actual_name = name_map.get(value)
    if actual_name and actual_name in node_map:
        return int(node_map[actual_name])
    return None
