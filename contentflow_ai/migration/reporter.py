from __future__ import annotations

import csv
import json
from pathlib import Path

from openpyxl import Workbook

from .models import Issue, PreflightReport


class ReportGenerator:
    def __init__(self, output_dir: str | Path = "reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_all(self, report: PreflightReport, *, stem: str = "migration_preflight") -> dict[str, Path]:
        paths = {
            "json": self.write_json(report, f"{stem}.json"),
            "markdown": self.write_markdown(report, f"{stem}.md"),
            "csv": self.write_csv(report.issues, f"{stem}_issues.csv"),
            "xlsx": self.write_xlsx(report.issues, f"{stem}_issues.xlsx"),
        }
        return paths

    def write_json(self, report: PreflightReport, filename: str) -> Path:
        path = self.output_dir / filename
        path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_markdown(self, report: PreflightReport, filename: str) -> Path:
        path = self.output_dir / filename
        summary = report.summary
        lines = [
            f"# {report.project_name} – Migration Preflight Report",
            "",
            "## Executive summary",
            "",
            f"- **Decision:** {summary.decision}",
            f"- **Readiness score:** {summary.readiness_score}/100",
            f"- **Workspace rows:** {summary.workspace_rows}",
            f"- **File rows:** {summary.file_rows}",
            f"- **Blocking issues:** {summary.blocking_count}",
            f"- **Errors:** {summary.error_count}",
            f"- **Warnings:** {summary.warning_count}",
            f"- **Generated at:** {report.generated_at}",
            f"- **Mode:** {report.mode}",
            "",
            "## Recommendation",
            "",
            self._recommendation(summary.decision),
            "",
            "## Issue overview",
            "",
        ]
        if not report.issues:
            lines.append("No issues found. The input is ready for the next reviewed dry-run step.")
        else:
            lines.extend([
                "| Severity | Code | Row type | Row | Field | Blocking | Message | Suggestion |",
                "|---|---|---|---:|---|---|---|---|",
            ])
            for issue in sorted(report.issues, key=_issue_sort_key):
                lines.append(
                    "| {severity} | `{code}` | {row_type} | {row} | {field} | {blocking} | {message} | {suggestion} |".format(
                        severity=issue.severity.upper(),
                        code=issue.code,
                        row_type=issue.row_type,
                        row=issue.row_index or "",
                        field=issue.field or "",
                        blocking="yes" if issue.blocking else "no",
                        message=_escape_table(issue.message),
                        suggestion=_escape_table(issue.suggestion),
                    )
                )
        lines.extend([
            "",
            "## Next steps",
            "",
            "1. Fix all blocking errors in the Excel or local source folder.",
            "2. Re-run `analyze` until there are no local blocking issues.",
            "3. Run `preflight` with Content Server read-only checks enabled.",
            "4. Only after reviewed approval, run dry-run/import execution in a controlled batch.",
            "",
            "## Metadata",
            "",
            "```json",
            json.dumps(report.metadata, ensure_ascii=False, indent=2),
            "```",
            "",
        ])
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def write_csv(self, issues: list[Issue], filename: str) -> Path:
        path = self.output_dir / filename
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=_issue_fieldnames())
            writer.writeheader()
            for issue in sorted(issues, key=_issue_sort_key):
                writer.writerow(issue.to_dict())
        return path

    def write_xlsx(self, issues: list[Issue], filename: str) -> Path:
        path = self.output_dir / filename
        wb = Workbook()
        ws = wb.active
        ws.title = "Issues"
        headers = _issue_fieldnames()
        ws.append(headers)
        for issue in sorted(issues, key=_issue_sort_key):
            data = issue.to_dict()
            ws.append([data.get(header) for header in headers])
        ws.auto_filter.ref = ws.dimensions
        ws.freeze_panes = "A2"
        for column_cells in ws.columns:
            max_length = max(len(str(cell.value or "")) for cell in column_cells)
            ws.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 70)
        wb.save(path)
        return path

    @staticmethod
    def _recommendation(decision: str) -> str:
        if decision == "GO":
            return "**GO.** The source is suitable for the next dry-run/import planning step."
        if decision == "GO_WITH_WARNINGS":
            return "**GO WITH WARNINGS.** The batch can proceed only after the warnings are reviewed and accepted."
        return "**NO-GO.** Blocking issues must be fixed before any write operation is allowed."


def _issue_fieldnames() -> list[str]:
    return ["severity", "code", "row_type", "row_index", "field", "blocking", "message", "suggestion", "value"]


def _issue_sort_key(issue: Issue) -> tuple[int, int, str]:
    severity_rank = {"error": 0, "warning": 1, "info": 2}
    return (severity_rank.get(issue.severity, 9), issue.row_index or 0, issue.code)


def _escape_table(value: str) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")
