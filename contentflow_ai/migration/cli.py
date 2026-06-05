from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .cleanup import CleanupExecutor, CleanupPlanner
from .config import load_config
from .excel_parser import parse_workbook
from .import_engine import ImportEngine, ImportExecutionReport
from .logging_setup import setup_logging
from .reporter import ReportGenerator
from .validator import PreflightValidator


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _load_dotenv(args.env_file)

    if args.command in {"analyze", "preflight"}:
        return _run_preflight(args, include_content_server=args.command == "preflight")
    if args.command == "dry-run":
        return _run_import(args, execute=False)
    if args.command == "execute":
        return _run_import(args, execute=True)
    if args.command == "cleanup-plan":
        return _run_cleanup_plan(args)
    if args.command == "cleanup-execute":
        return _run_cleanup_execute(args)
    parser.print_help()
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="contentflow", description="ContentFlow AI Migration Copilot CLI")
    parser.add_argument("--env-file", default=".env", help="Optional .env file with OTCS credentials/secrets.")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in [
        ("analyze", "Validate Excel and local source files only. No Content Server connection."),
        ("preflight", "Analyze and run read-only Content Server checks."),
        ("dry-run", "Build an import plan without write operations."),
        ("execute", "Execute the import. Requires --yes."),
    ]:
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("xlsx", help="Migration workbook path.")
        cmd.add_argument("--config", required=True, help="JSON config path.")
        cmd.add_argument("--reports-dir", default="reports", help="Report output directory.")
        if name == "execute":
            cmd.add_argument("--yes", action="store_true", help="Required confirmation for write operations.")
    cleanup_plan = sub.add_parser("cleanup-plan", help="Build a read-only cleanup plan from an execution JSON report.")
    cleanup_plan.add_argument("execution_report_json", help="Execution report JSON path.")
    cleanup_plan.add_argument("--config", required=True, help="JSON config path.")
    cleanup_plan.add_argument("--reports-dir", default="reports", help="Report output directory.")

    cleanup_execute = sub.add_parser("cleanup-execute", help="Execute cleanup from an execution JSON report. Requires --yes.")
    cleanup_execute.add_argument("execution_report_json", help="Execution report JSON path.")
    cleanup_execute.add_argument("--config", required=True, help="JSON config path.")
    cleanup_execute.add_argument("--reports-dir", default="reports", help="Report output directory.")
    cleanup_execute.add_argument("--yes", action="store_true", help="Required confirmation for cleanup write operations.")
    return parser


def _run_preflight(args: argparse.Namespace, *, include_content_server: bool) -> int:
    cfg = load_config(args.config, dry_run=True)
    workbook = parse_workbook(args.xlsx, cfg)
    report = PreflightValidator(cfg).validate(workbook, include_content_server=include_content_server)
    paths = ReportGenerator(args.reports_dir).write_all(report, stem=Path(args.xlsx).stem + "_preflight")

    print(f"Decision: {report.summary.decision}")
    print(f"Readiness score: {report.summary.readiness_score}/100")
    print(f"Issues: {len(report.issues)} ({report.summary.error_count} errors, {report.summary.warning_count} warnings)")
    for kind, path in paths.items():
        print(f"{kind}: {path}")
    return 1 if report.summary.decision == "NO_GO" else 0


def _run_import(args: argparse.Namespace, *, execute: bool) -> int:
    if execute and not args.yes:
        print("Refusing execute mode without --yes. Run analyze/preflight first and review the reports.", file=sys.stderr)
        return 2

    cfg = load_config(args.config, dry_run=not execute)
    logger, full_log, error_log = setup_logging(Path(args.xlsx).stem)
    logger.info("Starting import command execute=%s xlsx=%s", execute, args.xlsx)

    workbook = parse_workbook(args.xlsx, cfg)
    report = PreflightValidator(cfg).validate(workbook, include_content_server=False)
    if execute and report.summary.decision == "NO_GO":
        ReportGenerator(args.reports_dir).write_all(report, stem=Path(args.xlsx).stem + "_blocked_preflight")
        print("Execute blocked because local preflight has blocking errors.", file=sys.stderr)
        print(f"Full log: {full_log}")
        print(f"Error log: {error_log}")
        return 1

    stats = ImportEngine(cfg).run(workbook, execute=execute)
    execution_report = ImportExecutionReport.create(
        cfg=cfg,
        workbook=workbook,
        mode="execute" if execute else "dry-run",
        stats=stats,
    )
    execution_paths = ReportGenerator(args.reports_dir).write_execution_all(
        execution_report,
        stem=Path(args.xlsx).stem,
    )
    logger.info("Import stats: %s", stats)
    print(stats)
    for kind, path in execution_paths.items():
        print(f"{kind}: {path}")
    print(f"Full log: {full_log}")
    print(f"Error log: {error_log}")
    return 0 if stats.ws_failed == 0 and stats.f_failed == 0 else 1


def _run_cleanup_plan(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, dry_run=True)
    report = CleanupPlanner(cfg).plan(args.execution_report_json)
    paths = ReportGenerator(args.reports_dir).write_cleanup_all(
        report,
        execution_stem=Path(args.execution_report_json).stem,
        phase="plan",
    )
    _print_cleanup_report(report)
    for kind, path in paths.items():
        print(f"{kind}: {path}")
    return 0


def _run_cleanup_execute(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Refusing cleanup-execute without --yes. Review cleanup-plan first.", file=sys.stderr)
        return 2
    cfg = load_config(args.config, dry_run=False)
    try:
        report = CleanupExecutor(cfg).execute(args.execution_report_json)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    paths = ReportGenerator(args.reports_dir).write_cleanup_all(
        report,
        execution_stem=Path(args.execution_report_json).stem,
        phase="execution",
    )
    _print_cleanup_report(report)
    for kind, path in paths.items():
        print(f"{kind}: {path}")
    return 0 if report.stats.relations_failed == 0 and report.stats.workspaces_failed == 0 else 1


def _print_cleanup_report(report) -> None:
    print(f"Cleanup mode: {report.mode}")
    print(f"Source execution report: {report.source_execution_report}")
    print("Relations:")
    for row in report.relations:
        print(
            f"- {row.status}: {row.source_node_id or '?'} -> {row.target_node_id or '?'} "
            f"({row.relation_type})"
        )
    print("Workspaces:")
    for row in report.workspaces:
        print(f"- {row.status}: {row.node_id or '?'} {row.workspace_name} [{row.original_status}]")
    if report.files:
        print("Uploaded files (informational):")
        for row in report.files:
            print(f"- {row.title} under parent {row.parent_node_id or '?'} [{row.original_status}]")
    for warning in report.warnings:
        print(f"Warning: {warning}")


def _load_dotenv(path: str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if __name__ == "__main__":
    raise SystemExit(main())
