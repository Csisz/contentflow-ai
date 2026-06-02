from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from .config import MigrationConfig
from .cs_client import CSClient, CSClientError
from .models import Issue, MigrationWorkbook, PreflightReport
from .utils import get_mime, has_invalid_name_chars, supported_mime_hints


class PreflightValidator:
    def __init__(self, cfg: MigrationConfig, *, cs_client: CSClient | None = None):
        self.cfg = cfg
        self.cs_client = cs_client

    def validate(
        self,
        workbook: MigrationWorkbook,
        *,
        include_content_server: bool = False,
    ) -> PreflightReport:
        issues: list[Issue] = []
        issues.extend(self._validate_config())
        issues.extend(self._validate_workbook_shape(workbook))
        issues.extend(self._validate_workspaces(workbook))
        issues.extend(self._validate_files(workbook))
        if include_content_server:
            issues.extend(self._validate_content_server(workbook))

        return PreflightReport.create(
            project_name=self.cfg.project_name,
            xlsx_path=workbook.xlsx_path,
            mode="preflight" if include_content_server else "analyze",
            workspace_count=len(workbook.workspaces),
            file_count=len(workbook.files),
            issues=issues,
            metadata={
                "sheets": workbook.sheet_names,
                "ssl_verify": self.cfg.ssl_verify,
                "dry_run_default": self.cfg.dry_run,
                "on_duplicate": self.cfg.on_duplicate,
            },
        )

    def _validate_config(self) -> list[Issue]:
        issues: list[Issue] = []
        if not self.cfg.base_url.startswith(("https://", "http://")):
            issues.append(
                Issue(
                    severity="error",
                    code="INVALID_BASE_URL",
                    row_type="config",
                    field="base_url",
                    message="Content Server base_url must start with http:// or https://.",
                    suggestion="Use the full OTCS URL, for example https://server/otcs/cs.exe.",
                    blocking=True,
                    value=self.cfg.base_url,
                )
            )
        if self.cfg.username.lower() in {"admin", "administrator"}:
            issues.append(
                Issue(
                    severity="warning",
                    code="ADMIN_USER_IN_CONFIG",
                    row_type="config",
                    field="username",
                    message="The config uses a generic admin-style username.",
                    suggestion="Use a dedicated technical user with the minimum required permissions.",
                    value=self.cfg.username,
                )
            )
        if not self.cfg.dry_run:
            issues.append(
                Issue(
                    severity="warning",
                    code="DRY_RUN_DISABLED",
                    row_type="config",
                    field="dry_run",
                    message="dry_run is disabled in the config.",
                    suggestion="Keep dry_run enabled until the preflight report is clean and reviewed.",
                )
            )
        if self.cfg.on_duplicate not in {"skip", "new_version", "fail"}:
            issues.append(
                Issue(
                    severity="error",
                    code="INVALID_DUPLICATE_POLICY",
                    row_type="config",
                    field="on_duplicate",
                    message=f"Unsupported duplicate policy: {self.cfg.on_duplicate}.",
                    suggestion="Use one of: skip, new_version, fail.",
                    blocking=True,
                    value=self.cfg.on_duplicate,
                )
            )
        return issues

    def _validate_workbook_shape(self, workbook: MigrationWorkbook) -> list[Issue]:
        issues: list[Issue] = []
        for sheet_name in [self.cfg.ws_sheet, self.cfg.file_sheet]:
            if sheet_name not in workbook.sheet_names:
                issues.append(
                    Issue(
                        severity="error",
                        code="MISSING_SHEET",
                        row_type="workbook",
                        field=sheet_name,
                        message=f"Required sheet is missing: {sheet_name}.",
                        suggestion="Check the config sheet names or rename the Excel sheet.",
                        blocking=True,
                    )
                )
        if not workbook.workspaces and not workbook.files:
            issues.append(
                Issue(
                    severity="warning",
                    code="EMPTY_IMPORT_SCOPE",
                    row_type="workbook",
                    message="No workspace or file rows were parsed from the workbook.",
                    suggestion="Check start rows and column indexes in the config.",
                )
            )
        return issues

    def _validate_workspaces(self, workbook: MigrationWorkbook) -> list[Issue]:
        issues: list[Issue] = []
        title_counts = Counter((ws.location, ws.title.lower()) for ws in workbook.workspaces if ws.title)
        for ws in workbook.workspaces:
            if not ws.title:
                issues.append(
                    Issue("error", "MISSING_WORKSPACE_TITLE", "workspace", "Workspace title is empty.",
                          "Fill the title cell or remove the row.", ws.row_index, "title", True)
                )
            if not ws.location:
                issues.append(
                    Issue("error", "MISSING_WORKSPACE_LOCATION", "workspace", "Workspace location is empty.",
                          "Fill the target Content Server location.", ws.row_index, "location", True)
                )
            if ws.title and has_invalid_name_chars(ws.title):
                issues.append(
                    Issue("error", "INVALID_WORKSPACE_NAME", "workspace", "Workspace title contains invalid path characters.",
                          "Remove characters like \\, /, :, *, ?, <, >, |.", ws.row_index, "title", True, ws.title)
                )
            if ws.title and len(ws.title) > 248:
                issues.append(
                    Issue("warning", "LONG_WORKSPACE_NAME", "workspace", "Workspace title is very long.",
                          "Shorten the name to reduce Content Server and Windows path risks.", ws.row_index, "title", value=ws.title)
                )
            if ws.title and title_counts[(ws.location, ws.title.lower())] > 1:
                issues.append(
                    Issue("warning", "DUPLICATE_WORKSPACE_IN_EXCEL", "workspace",
                          "Duplicate workspace title under the same target location.",
                          "Review whether this should be one workspace, a renamed workspace, or skipped.",
                          ws.row_index, "title", value=ws.title)
                )
            for key, field_cfg in self.cfg.category_fields.items():
                value = ws.cat_values.get(key)
                if field_cfg.required and not value:
                    issues.append(
                        Issue("error", "MISSING_REQUIRED_CATEGORY_VALUE", "workspace",
                              f"Required category field is empty: {key}.",
                              "Fill the mapped Excel column or mark the field non-required in config if appropriate.",
                              ws.row_index, key, True)
                    )
        return issues

    def _validate_files(self, workbook: MigrationWorkbook) -> list[Issue]:
        issues: list[Issue] = []
        file_counts = Counter((file.location, file.title.lower()) for file in workbook.files if file.title)
        supported_hints = supported_mime_hints()
        by_path: dict[str, list[int]] = defaultdict(list)

        for file in workbook.files:
            if not file.title:
                issues.append(Issue("error", "MISSING_FILE_TITLE", "file", "File title is empty.",
                                    "Fill the title cell or remove the row.", file.row_index, "title", True))
            if not file.location:
                issues.append(Issue("error", "MISSING_FILE_LOCATION", "file", "File target location is empty.",
                                    "Fill the target Content Server location.", file.row_index, "location", True))
            if not file.src:
                issues.append(Issue("error", "MISSING_FILE_SOURCE", "file", "Source file path is empty.",
                                    "Fill the source file column.", file.row_index, "src", True))
            if file.title and has_invalid_name_chars(file.title):
                issues.append(Issue("error", "INVALID_FILE_NAME", "file", "File title contains invalid path characters.",
                                    "Remove characters like \\, /, :, *, ?, <, >, |.", file.row_index, "title", True, file.title))
            if file.title and len(file.title) > 248:
                issues.append(Issue("warning", "LONG_FILE_NAME", "file", "File title is very long.",
                                    "Shorten the name to reduce Content Server and Windows path risks.", file.row_index, "title", value=file.title))
            if file.title and file_counts[(file.location, file.title.lower())] > 1:
                issues.append(Issue("warning", "DUPLICATE_FILE_IN_TARGET", "file",
                                    "Duplicate file title in the same target location.",
                                    "Decide whether to skip, version, rename, or split the target location.",
                                    file.row_index, "title", value=file.title))

            if file.local_path:
                by_path[str(Path(file.local_path))].append(file.row_index)
                path = Path(file.local_path)
                if not path.exists():
                    issues.append(Issue("error", "MISSING_SOURCE_FILE", "file", "Source file does not exist locally.",
                                        "Check local_file_root and the Excel source file name.", file.row_index, "local_path", True, file.local_path))
                elif not path.is_file():
                    issues.append(Issue("error", "SOURCE_IS_NOT_FILE", "file", "Source path is not a regular file.",
                                        "Point the row to an actual file.", file.row_index, "local_path", True, file.local_path))
                else:
                    try:
                        size = path.stat().st_size
                        if size == 0:
                            issues.append(Issue("error", "ZERO_BYTE_FILE", "file", "Source file is zero bytes.",
                                                "Replace or remove the empty file.", file.row_index, "local_path", True, file.local_path))
                        elif size > 1024 * 1024 * 1024:
                            issues.append(Issue("warning", "VERY_LARGE_FILE", "file", "Source file is larger than 1 GB.",
                                                "Check upload limits and network stability before execute mode.", file.row_index, "local_path", value=file.local_path))
                    except OSError as exc:
                        issues.append(Issue("error", "SOURCE_FILE_NOT_READABLE", "file", f"Cannot read source file metadata: {exc}",
                                            "Check file permissions.", file.row_index, "local_path", True, file.local_path))

                if file.mime_hint:
                    hint = file.mime_hint.lower().lstrip(".")
                    if hint not in supported_hints and "/" not in hint:
                        issues.append(Issue("warning", "UNKNOWN_MIME_HINT", "file", "MIME hint is not in the supported extension map.",
                                            f"Use one of {sorted(supported_hints)} or leave empty for auto-detection.",
                                            file.row_index, "mime_hint", value=file.mime_hint))
                    detected = get_mime(file.local_path, file.mime_hint)
                    if path.suffix and hint and hint in supported_hints and not detected:
                        issues.append(Issue("info", "MIME_AUTO_DETECT", "file", "MIME type will be auto-detected.",
                                            "No action needed unless Content Server rejects the file.", file.row_index, "mime_hint"))

        for local_path, rows in by_path.items():
            if len(rows) > 1:
                issues.append(Issue("info", "SOURCE_FILE_REFERENCED_MULTIPLE_TIMES", "file",
                                    f"The same source file is referenced by multiple Excel rows: {rows}.",
                                    "This may be intentional; otherwise check duplicate input rows.", rows[0], "local_path", value=local_path))
        return issues

    def _validate_content_server(self, workbook: MigrationWorkbook) -> list[Issue]:
        issues: list[Issue] = []
        client = self.cs_client or CSClient(self.cfg)
        try:
            client.authenticate()
            root = client.get_node(self.cfg.enterprise_node_id)
            if root is None:
                issues.append(Issue("error", "ENTERPRISE_ROOT_NOT_FOUND", "content_server",
                                    "Configured enterprise_node_id was not found.",
                                    "Check enterprise_node_id and user permissions.", field="enterprise_node_id", blocking=True))
                return issues
        except (CSClientError, Exception) as exc:
            issues.append(Issue("error", "CONTENT_SERVER_CONNECTION_FAILED", "content_server",
                                f"Cannot connect/authenticate to Content Server: {exc}",
                                "Check URL, credentials, network, SSL and permissions.", blocking=True))
            return issues

        unique_locations = sorted({ws.location for ws in workbook.workspaces if ws.location} | {f.location for f in workbook.files if f.location})
        for location in unique_locations:
            try:
                node_id = client.resolve_existing_path(location)
                if node_id is None:
                    issues.append(Issue("warning", "TARGET_LOCATION_MISSING", "content_server",
                                        "Target location does not currently exist in Content Server.",
                                        "In execute mode missing folders may be auto-created; review whether this is intended.",
                                        field="location", value=location))
            except Exception as exc:
                issues.append(Issue("warning", "TARGET_LOCATION_CHECK_FAILED", "content_server",
                                    f"Could not verify target location: {exc}",
                                    "Review path spelling and permissions.", field="location", value=location))
        return issues
