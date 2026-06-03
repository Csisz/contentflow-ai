from __future__ import annotations

from dataclasses import dataclass, field

from .config import MigrationConfig
from .cs_client import CSClient
from .models import MigrationWorkbook


@dataclass(slots=True)
class ImportStats:
    ws_created: int = 0
    ws_existing: int = 0
    ws_failed: int = 0
    f_uploaded: int = 0
    f_versioned: int = 0
    f_skipped: int = 0
    f_failed: int = 0
    details: list[str] = field(default_factory=list)


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
            return stats

        self.client.authenticate()
        for ws in workbook.workspaces:
            try:
                parent_id = self.client.resolve_or_create_path(ws.location)
                _node_id, created, actual_name = self.client.create_or_get_workspace(parent_id, ws.title, ws.cat_values)
                if created:
                    stats.ws_created += 1
                else:
                    stats.ws_existing += 1
                if actual_name != ws.title:
                    stats.details.append(f"Workspace name remapped: {ws.title} -> {actual_name}")
            except Exception as exc:  # noqa: BLE001 - migration batch should continue and collect errors
                stats.ws_failed += 1
                stats.details.append(f"Workspace row {ws.row_index} failed: {exc}")

        for file in workbook.files:
            try:
                original_location = file.location
                location = self.client.remap_file_location(file.location)
                if location != original_location:
                    stats.details.append(f"File location remapped: {original_location} -> {location}")
                parent_id = self.client.resolve_or_create_path(location)
                result = self.client.upload_file(parent_id, file.title, file.local_path, file.mime_hint)
                if result == "uploaded":
                    stats.f_uploaded += 1
                elif result == "versioned":
                    stats.f_versioned += 1
                elif result == "skipped":
                    stats.f_skipped += 1
                else:
                    stats.f_failed += 1
            except Exception as exc:  # noqa: BLE001
                stats.f_failed += 1
                stats.details.append(f"File row {file.row_index} failed: {exc}")
        return stats
