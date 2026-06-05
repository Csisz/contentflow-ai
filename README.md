# ContentFlow AI
## On-prem OCR Intake + Content Server Migration Copilot

ContentFlow AI is a modular internal innovation tool for OpenText Content Server environments. The first MVP focuses on **Migration Copilot**: Excel-based migration validation, read-only preflight checks and readiness reporting before any write operation is allowed.

This starter repo was refactored from the legacy `ot-migration-main.zip` importer concept. The old single-script approach is split into separate modules for configuration, Excel parsing, Content Server API access, validation, reporting and import execution.

## Current MVP scope

- Parse migration XLSX workbooks from configurable Workspace and File sheets.
- Validate workbook structure, required metadata, duplicate rows, invalid names, missing local files and MIME hints.
- Generate preflight reports in JSON, Markdown, CSV and XLSX.
- Keep Content Server write operations separated from read-only preflight.
- Use environment-based secrets instead of committed passwords.
- Prepare the package for later OCR Intake Lite and AI suggestion modules.

## Project structure

```text
contentflow_ai/
  migration/
    cli.py              # CLI entry point
    config.py           # JSON config + env placeholder resolution
    excel_parser.py     # XLSX parser
    cs_client.py        # OpenText Content Server REST client
    validator.py        # Migration Copilot preflight validation
    reporter.py         # JSON / Markdown / CSV / XLSX report generation
    import_engine.py    # Dry-run / execute migration engine
    models.py           # Shared dataclasses
    utils.py            # MIME and naming helpers
  dashboard/            # Local Flask dashboard for operating the CLI
  ocr/                  # OCR Intake Lite placeholder
  ai/                   # Optional AI advisor placeholder
config/
  config.template.json  # Safe config template without secrets
tests/                  # Unit tests
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
copy config\config.template.json config\config.local.json
```

Edit `.env` and `config/config.local.json` locally. Do not commit either file.

The CLI and dashboard both read `config/config.local.json` by default. To use a different local config file:

```powershell
$env:CONTENTFLOW_CONFIG_PATH = "config\my-local-config.json"
```

Set Content Server credentials through environment variables or a local `.env` file:

```powershell
$env:OTCS_BASE_URL = "https://content-server.example.com"
$env:OTCS_USERNAME = "migration-user"
$env:OTCS_PASSWORD = "your-password"
```

The dashboard shows whether these values are set, but it never renders the password value.

## Safe first run: local analyze only

```powershell
python -m contentflow_ai.migration.cli analyze path\to\migration.xlsx --config config\config.local.json
```

This checks only Excel and local files. It does not connect to Content Server and does not write anything.

## Read-only Content Server preflight

```powershell
python -m contentflow_ai.migration.cli preflight path\to\migration.xlsx --config config\config.local.json
```

This authenticates to Content Server and verifies target paths where possible. It should still remain read-only.

## Dry-run import plan

```powershell
python -m contentflow_ai.migration.cli dry-run path\to\migration.xlsx --config config\config.local.json
```

## Optional related workspace sheet

To create official OpenText Business Workspace relations, add an optional `RelatedWorkspace` sheet:

| Column | Meaning |
|---|---|
| `source_workspace` | Source Business Workspace placeholder/name, generated name, or numeric node ID. |
| `target_workspace` | Target Business Workspace placeholder/name when `target_node_id` is empty. |
| `target_node_id` | Optional numeric target Business Workspace node ID. |
| `relation_type` | Optional `child` or `parent`; defaults to `child`. |
| `enabled` | Process only rows set to `1`, `true`, `yes`, `y`, or `igen`. |

Execute mode uses `POST /api/v2/businessworkspaces/{bw_id}/relateditems` with form fields `rel_bw_id` and `rel_type`. It does not create folders or normal Content Server nodes for related items.

## Execute mode

Execution is intentionally guarded:

```powershell
python -m contentflow_ai.migration.cli execute path\to\migration.xlsx --config config\config.local.json --yes
```

Before using execute mode, review the generated preflight reports and run on a small test batch.

## Local dashboard

Run the lightweight on-prem Flask dashboard from the project root:

```powershell
python -m contentflow_ai.dashboard.app
```

Open `http://127.0.0.1:5000` on the VM where Content Server access is available. The dashboard stays local and calls the existing Python CLI with the current Python executable; it does not use cloud services or store secrets.

The dashboard supports:

- Selecting existing `.xlsx` workbooks from the project root or `uploads/`.
- Uploading `.xlsx` workbooks into `uploads/`.
- Running analyze, Content Server preflight, dry-run, execute, cleanup-plan and cleanup-execute.
- Viewing generated reports from `reports/` and logs from `logs/`.
- Local branding settings for title, subtitle, logo and dashboard colors.

Execute actions require explicit confirmation in the browser:

- `Execute`: confirms that Content Server objects may be created or updated.
- `Cleanup Execute`: confirms that only objects listed in the selected execution report may be deleted.

Recommended workflow:

```text
analyze -> preflight -> dry-run -> execute -> review execution report -> cleanup-plan if needed
```

### Dashboard branding

Default dashboard branding is committed in `config/branding.example.json`. For local customization, create or save settings through the dashboard into:

```text
config/branding.local.json
```

`branding.local.json` is ignored by git and should stay local to the VM. The dashboard loads branding in this order:

```text
config/branding.local.json -> config/branding.example.json -> hardcoded defaults
```

Open `Settings` in the dashboard to edit:

- title and subtitle
- primary and secondary colors
- logo URL
- header style
- local uploaded logo

Uploaded logos are stored under `branding/`, which is ignored by git except for `branding/.gitkeep`. Allowed logo file types are `png`, `jpg`, `jpeg`, `svg` and `webp`.

The `Try auto-detect from Content Server` button uses only `OTCS_BASE_URL` and performs a best-effort unauthenticated read of the configured Content Server root/login page. It may preview a page title, logo image reference or favicon. This is fallback-based and optional; Content Server appearance APIs are not part of the documented REST API in this project, so auto-detect must not be treated as core functionality.

## Security rules

- Never commit `.env`, `config.local.json`, live XLSX files, customer documents or generated reports.
- Never commit `config/branding.local.json` or uploaded local branding assets from `branding/`.
- Use a dedicated technical Content Server user, not a generic admin account.
- Keep AI features optional and prefer on-prem/internal models.
- Run `analyze` and `preflight` before any execute operation.
- Treat generated reports as potentially sensitive because they may contain filenames and target paths.

## Next development steps

1. Add more unit tests around edge-case Excel mappings.
2. Add mock Content Server tests for preflight path verification.
3. Expand dashboard integration tests for report viewing and upload handling.
4. Add OCR Intake Lite pipeline skeleton: input folder, OCR text extraction, metadata suggestion, human review.
5. Add optional AI advisor interface with local model support.
