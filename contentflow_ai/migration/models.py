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
    total_issues: int = 0
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    blocking_count: int = 0
    blocking_issue_count: int = 0
    readiness_score: int = 100
    decision: Literal["GO", "GO_WITH_WARNINGS", "NO_GO"] = "GO"
    top_risks: list[str] = field(default_factory=list)
    recommended_next_steps: list[str] = field(default_factory=list)


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
            total_issues=len(issues),
            error_count=sum(1 for i in issues if i.severity == "error"),
            warning_count=sum(1 for i in issues if i.severity == "warning"),
            info_count=sum(1 for i in issues if i.severity == "info"),
            blocking_count=sum(1 for i in issues if i.blocking),
        )
        summary.blocking_issue_count = summary.blocking_count
        summary.readiness_score = calculate_readiness_score(summary, workspace_count, file_count)
        summary.decision = readiness_decision(summary)
        summary.top_risks = top_risks(issues)
        summary.recommended_next_steps = recommended_next_steps(summary)

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
            "readiness": asdict(self.summary),
            "issues": [issue.to_dict() for issue in self.issues],
            "metadata": self.metadata,
        }


def readiness_decision(summary: PreflightSummary) -> Literal["GO", "GO_WITH_WARNINGS", "NO_GO"]:
    if summary.blocking_issue_count or summary.blocking_count or summary.readiness_score < 70:
        return "NO_GO"
    if summary.readiness_score < 90 or summary.warning_count:
        return "GO_WITH_WARNINGS"
    return "GO"


def recommended_next_steps(summary: PreflightSummary) -> list[str]:
    if summary.decision == "GO":
        return [
            "Review the readiness report with the migration owner.",
            "Run Content Server preflight if this report was created by Analyze.",
            "Proceed to dry-run before any controlled execution.",
        ]
    if summary.decision == "GO_WITH_WARNINGS":
        return [
            "Review and accept or remediate all warnings.",
            "Re-run Analyze or Preflight after updates.",
            "Proceed to dry-run only after stakeholder approval.",
        ]
    return [
        "Fix all blocking issues before migration execution.",
        "Re-run Analyze until blocking issues are cleared.",
        "Run Content Server preflight before dry-run or execution.",
    ]


def top_risks(issues: list[Issue], limit: int = 5) -> list[str]:
    risks = []
    seen = set()
    for issue in sorted(issues, key=_risk_sort_key):
        if issue.severity == "info" and not issue.blocking:
            continue
        risk = f"{issue.code}: {issue.message}"
        if issue.suggestion:
            risk = f"{risk} Recommendation: {issue.suggestion}"
        if risk in seen:
            continue
        seen.add(risk)
        risks.append(risk)
        if len(risks) >= limit:
            break
    return risks or ["No material migration readiness risks were detected."]


def _risk_sort_key(issue: Issue) -> tuple[int, int, str]:
    if issue.blocking:
        severity_rank = 0
    elif issue.severity == "error":
        severity_rank = 1
    elif issue.severity == "warning":
        severity_rank = 2
    else:
        severity_rank = 3
    return (severity_rank, issue.row_index or 0, issue.code)


def calculate_readiness_score(summary: PreflightSummary, workspace_count: int, file_count: int) -> int:
    total_rows = max(workspace_count + file_count, 1)
    scale = max(total_rows ** 0.2, 1)
    penalty = 0
    penalty += summary.blocking_issue_count * 35
    penalty += max(summary.error_count - summary.blocking_issue_count, 0) * 18
    penalty += summary.warning_count * 7
    penalty += summary.info_count * 1
    scaled_penalty = round(penalty / scale)
    return max(0, min(100, 100 - scaled_penalty))
