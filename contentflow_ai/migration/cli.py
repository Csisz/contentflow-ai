from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import load_config
from .excel_parser import parse_workbook
from .import_engine import ImportEngine
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
    logger.info("Import stats: %s", stats)
    print(stats)
    print(f"Full log: {full_log}")
    print(f"Error log: {error_log}")
    return 0 if stats.ws_failed == 0 and stats.f_failed == 0 else 1


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
