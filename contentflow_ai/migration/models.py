from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

Severity = Literal["error", "warning", "info"]
RowType = Literal["config", "workbook", "workspace", "file", "related_workspace", "content_server"]


@dataclass(slots=True)
class CategoryFieldConfig:
    key: str
    attr_id: int | str
    col: int | None = None
    col_start: int | None = None
    col_end: int | None = None
    multi_value: bool = False
    required: bool = False
    value_map: dict[str, str] = field(default_factory=dict)
    comment: str = ""


@dataclass(slots=True)
class WorkspaceRow:
    row_index: int
    location: str
    title: str
    cat_values: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FileRow:
    row_index: int
    location: str
    title: str
    local_path: str
    src: str = ""
    mime_hint: str = ""
    version: str = ""


@dataclass(slots=True)
class RelatedWorkspaceRow:
    row_index: int
    source_workspace: str
    target_workspace: str
    target_node_id: int | None = None
    relation_type: str = ""
    enabled: bool = False


@dataclass(slots=True)
class MigrationWorkbook:
    xlsx_path: Path
    workspaces: list[WorkspaceRow]
    files: list[FileRow]
    sheet_names: list[str]
    related_workspaces: list[RelatedWorkspaceRow] = field(default_factory=list)


@dataclass(slots=True)
class Issue:
    severity: Severity
    code: str
    row_type: RowType
    message: str
    suggestion: str = ""
    row_index: int | None = None
    field: str | None = None
    blocking: bool = False
    value: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PreflightSummary:
    workspace_rows: int = 0
    file_rows: int = 0
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    blocking_count: int = 0
    readiness_score: int = 100
    decision: Literal["GO", "GO_WITH_WARNINGS", "NO_GO"] = "GO"


@dataclass(slots=True)
class PreflightReport:
    project_name: str
    generated_at: str
    xlsx_path: str
    mode: str
    summary: PreflightSummary
    issues: list[Issue]
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        project_name: str,
        xlsx_path: Path,
        mode: str,
        workspace_count: int,
        file_count: int,
        issues: list[Issue],
        metadata: dict[str, Any] | None = None,
    ) -> "PreflightReport":
        summary = PreflightSummary(
            workspace_rows=workspace_count,
            file_rows=file_count,
            error_count=sum(1 for i in issues if i.severity == "error"),
            warning_count=sum(1 for i in issues if i.severity == "warning"),
            info_count=sum(1 for i in issues if i.severity == "info"),
            blocking_count=sum(1 for i in issues if i.blocking),
        )
        summary.readiness_score = calculate_readiness_score(summary, workspace_count, file_count)
        if summary.blocking_count or summary.error_count:
            summary.decision = "NO_GO"
        elif summary.warning_count:
            summary.decision = "GO_WITH_WARNINGS"
        else:
            summary.decision = "GO"

        return cls(
            project_name=project_name,
            generated_at=datetime.now(timezone.utc).isoformat(),
            xlsx_path=str(xlsx_path),
            mode=mode,
            summary=summary,
            issues=issues,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "generated_at": self.generated_at,
            "xlsx_path": self.xlsx_path,
            "mode": self.mode,
            "summary": asdict(self.summary),
            "issues": [issue.to_dict() for issue in self.issues],
            "metadata": self.metadata,
        }


def calculate_readiness_score(summary: PreflightSummary, workspace_count: int, file_count: int) -> int:
    total_rows = max(workspace_count + file_count, 1)
    penalty = 0
    penalty += summary.blocking_count * 18
    penalty += summary.error_count * 10
    penalty += summary.warning_count * 3
    # Scale down very large batches slightly so one warning in 10k rows does not dominate.
    scaled_penalty = int(penalty / max(total_rows ** 0.25, 1))
    return max(0, min(100, 100 - scaled_penalty))
