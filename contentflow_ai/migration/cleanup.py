from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import MigrationConfig
from .cs_client import CSClient


@dataclass(slots=True)
class CleanupRelationRow:
    row_index: int | None
    source_workspace: str
    source_node_id: int | None
    target_workspace: str
    target_node_id: int | None
    relation_type: str = "child"
    status: str = "planned"
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CleanupWorkspaceRow:
    row_index: int | None
    excel_title: str
    workspace_name: str
    node_id: int | None
    original_status: str
    status: str = "planned"
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CleanupFileInfoRow:
    row_index: int | None
    title: str
    parent_node_id: int | None
    original_status: str
    note: str = "Informational only; files are removed with their created workspace when applicable."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CleanupStats:
    relations_removed: int = 0
    relations_failed: int = 0
    workspaces_deleted: int = 0
    workspaces_failed: int = 0
    skipped: int = 0
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CleanupReport:
    project_name: str
    generated_at: str
    source_execution_report: str
    mode: str
    stats: CleanupStats
    relations: list[CleanupRelationRow]
    workspaces: list[CleanupWorkspaceRow]
    files: list[CleanupFileInfoRow]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "generated_at": self.generated_at,
            "source_execution_report": self.source_execution_report,
            "mode": self.mode,
            "summary": self.stats.to_dict(),
            "warnings": self.warnings,
            "relations": [row.to_dict() for row in self.relations],
            "workspaces": [row.to_dict() for row in self.workspaces],
            "files": [row.to_dict() for row in self.files],
        }


class CleanupPlanner:
    def __init__(self, cfg: MigrationConfig):
        self.cfg = cfg

    def plan(self, execution_report_json: str | Path) -> CleanupReport:
        path = Path(execution_report_json)
        data = json.loads(path.read_text(encoding="utf-8"))
        warnings: list[str] = []
        relations = _planned_relations(data, warnings)
        workspaces = _planned_workspaces(data, warnings)
        files = _planned_files(data)
        stats = CleanupStats(skipped=sum(1 for row in workspaces if row.status == "skipped"))
        return CleanupReport(
            project_name=str(data.get("project_name") or self.cfg.project_name),
            generated_at=datetime.now(timezone.utc).isoformat(),
            source_execution_report=str(path),
            mode="cleanup-plan",
            stats=stats,
            relations=relations,
            workspaces=workspaces,
            files=files,
            warnings=warnings,
        )


class CleanupExecutor:
    def __init__(self, cfg: MigrationConfig, client: CSClient | None = None):
        self.cfg = cfg
        self.client = client or CSClient(cfg)

    def execute(self, execution_report_json: str | Path) -> CleanupReport:
        report = CleanupPlanner(self.cfg).plan(execution_report_json)
        created_workspace_ids = [row.node_id for row in report.workspaces if row.original_status == "created" and row.node_id]
        if not created_workspace_ids:
            raise RuntimeError("Cleanup refused: execution report has no created workspace node IDs.")

        report.mode = "cleanup-execution"
        report.stats = CleanupStats()
        self.client.authenticate()
        self._remove_relations(report)
        self._delete_workspaces(report)
        return report

    def _remove_relations(self, report: CleanupReport) -> None:
        for row in report.relations:
            if row.status == "skipped":
                report.stats.skipped += 1
                continue
            if row.source_node_id is None or row.target_node_id is None:
                row.status = "skipped"
                row.error_message = "Relation source_node_id or target_node_id is missing."
                report.stats.skipped += 1
                continue
            try:
                self.client.delete_business_workspace_relation(row.source_node_id, row.target_node_id, row.relation_type)
                row.status = "removed"
                report.stats.relations_removed += 1
            except Exception as exc:  # noqa: BLE001 - cleanup must continue and report failures
                row.status = "failed"
                row.error_message = str(exc)
                report.stats.relations_failed += 1
                report.stats.details.append(f"Relation row {row.row_index} failed: {exc}")

    def _delete_workspaces(self, report: CleanupReport) -> None:
        for row in report.workspaces:
            if row.original_status != "created":
                row.status = "skipped"
                row.error_message = "Workspace was not created by the referenced execution report."
                report.stats.skipped += 1
                continue
            if row.node_id is None:
                row.status = "skipped"
                row.error_message = "Workspace node_id is missing."
                report.stats.skipped += 1
                continue
            try:
                self.client.delete_node(row.node_id)
                row.status = "deleted"
                report.stats.workspaces_deleted += 1
            except Exception as exc:  # noqa: BLE001
                row.status = "failed"
                row.error_message = str(exc)
                report.stats.workspaces_failed += 1
                report.stats.details.append(f"Workspace row {row.row_index} failed: {exc}")


def _planned_relations(data: dict[str, Any], warnings: list[str]) -> list[CleanupRelationRow]:
    rows: list[CleanupRelationRow] = []
    for item in data.get("related_workspaces", []):
        status = str(item.get("status") or "")
        source_id = _safe_int(item.get("source_node_id"))
        target_id = _safe_int(item.get("target_node_id"))
        if status != "created":
            warnings.append(f"Related row {item.get('row_index')} is not created and will be skipped: {status}.")
            planned_status = "skipped"
        elif source_id is None or target_id is None:
            warnings.append(f"Related row {item.get('row_index')} is missing source_node_id or target_node_id.")
            planned_status = "skipped"
        else:
            planned_status = "planned"
        rows.append(
            CleanupRelationRow(
                row_index=_safe_int(item.get("row_index")),
                source_workspace=str(item.get("source_workspace") or ""),
                source_node_id=source_id,
                target_workspace=str(item.get("target_workspace") or ""),
                target_node_id=target_id,
                relation_type=str(item.get("relation_type") or "child"),
                status=planned_status,
            )
        )
    return rows


def _planned_workspaces(data: dict[str, Any], warnings: list[str]) -> list[CleanupWorkspaceRow]:
    rows: list[CleanupWorkspaceRow] = []
    for item in data.get("workspaces", []):
        status = str(item.get("status") or "")
        node_id = _safe_int(item.get("node_id"))
        if status != "created":
            warnings.append(f"Workspace row {item.get('row_index')} is not created and will be skipped: {status}.")
            planned_status = "skipped"
        elif node_id is None:
            warnings.append(f"Workspace row {item.get('row_index')} is missing node_id.")
            planned_status = "skipped"
        else:
            planned_status = "planned"
        rows.append(
            CleanupWorkspaceRow(
                row_index=_safe_int(item.get("row_index")),
                excel_title=str(item.get("excel_title") or ""),
                workspace_name=str(item.get("workspace_name") or ""),
                node_id=node_id,
                original_status=status,
                status=planned_status,
            )
        )
    return rows


def _planned_files(data: dict[str, Any]) -> list[CleanupFileInfoRow]:
    rows: list[CleanupFileInfoRow] = []
    for item in data.get("files", []):
        status = str(item.get("status") or "")
        if status not in {"uploaded", "versioned"}:
            continue
        rows.append(
            CleanupFileInfoRow(
                row_index=_safe_int(item.get("row_index")),
                title=str(item.get("title") or ""),
                parent_node_id=_safe_int(item.get("parent_node_id")),
                original_status=status,
            )
        )
    return rows


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
