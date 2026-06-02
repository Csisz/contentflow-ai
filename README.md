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

## Execute mode

Execution is intentionally guarded:

```powershell
python -m contentflow_ai.migration.cli execute path\to\migration.xlsx --config config\config.local.json --yes
```

Before using execute mode, review the generated preflight reports and run on a small test batch.

## Security rules

- Never commit `.env`, `config.local.json`, live XLSX files, customer documents or generated reports.
- Use a dedicated technical Content Server user, not a generic admin account.
- Keep AI features optional and prefer on-prem/internal models.
- Run `analyze` and `preflight` before any execute operation.
- Treat generated reports as potentially sensitive because they may contain filenames and target paths.

## Next development steps

1. Add more unit tests around edge-case Excel mappings.
2. Add mock Content Server tests for preflight path verification.
3. Add a small Streamlit UI for Excel upload and report display.
4. Add OCR Intake Lite pipeline skeleton: input folder, OCR text extraction, metadata suggestion, human review.
5. Add optional AI advisor interface with local model support.
