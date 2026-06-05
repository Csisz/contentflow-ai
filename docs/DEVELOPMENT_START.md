# ContentFlow AI – fejlesztési indító státusz

## Döntés

A fejlesztést a Migration Copilot modullal érdemes kezdeni, nem az OCR-rel. Ennek oka, hogy a meglévő `cs_importer.py` már működő migrációs alapot ad, és ebből gyorsan lehet értékes, biztonságos, ügyfélnek is bemutatható preflight/readiness réteget építeni.

## Első sprint célja

Az első sprint célja egy tiszta, tesztelhető Python package kialakítása, amely:

- nem tartalmaz éles URL-t vagy jelszót,
- külön kezeli a Content Server klienst,
- külön kezeli az Excel parser logikát,
- külön kezeli a validációt,
- riportot generál a migrációs kockázatokról,
- alapértelmezés szerint read-only / dry-run szemléletű.

## Elkészült ebben a starter csomagban

- `contentflow_ai/migration/config.py`: biztonságos JSON config és `${ENV}` placeholder kezelés.
- `contentflow_ai/migration/excel_parser.py`: Workspace és File sheet parser.
- `contentflow_ai/migration/cs_client.py`: Content Server REST kliens, read-only és execute műveletekkel szétválaszthatóan.
- `contentflow_ai/migration/validator.py`: Migration Copilot preflight validator.
- `contentflow_ai/migration/reporter.py`: JSON, Markdown, CSV és XLSX riportgenerálás.
- `contentflow_ai/migration/import_engine.py`: dry-run / execute engine alap.
- `contentflow_ai/migration/cli.py`: `analyze`, `preflight`, `dry-run`, `execute` parancsok.
- `config/config.template.json`: titokmentes config minta.
- `.env.example`: lokális secret minta.
- `tests/`: első unit tesztek.

## Content Server path planning note

Preflight treats missing target folders as planned creation, not as a generic missing-location warning. The check is read-only: it plans the path under the configured migration root (`enterprise_node_id`) and reports the existing prefix plus the folders that execute mode will create. Execute mode still creates missing folders with `resolve_or_create_path()`.

## Javasolt fejlesztési sorrend innen

### Sprint 1/A – Repo tisztítás és futtatás

1. Másold be ezt a struktúrát az új GitHub repóba.
2. Ne vidd át a régi éles JSON-t.
3. Készíts lokális `.env` és `config/config.local.json` fájlt.
4. Futtasd a teszteket.
5. Próbáld ki egy szintetikus Excelen az `analyze` parancsot.

### Sprint 1/B – Preflight erősítése

1. Bővítsd a validátort Content Server read-only ellenőrzésekkel.
2. Adj hozzá mockolt CSClient teszteket.
3. Bővítsd a readiness scoring szabályokat.
4. Tegyél be go/no-go döntési logikát ügyfélbarát magyarázattal.

### Sprint 1/C – Riport és ügyfél-demo

1. Szépítsd a Markdown riportot.
2. Adj hozzá javítási javaslatokat soronként.
3. Készíts Streamlit PoC UI-t, amely feltöltött Excelből riportot mutat.

### Sprint 2 – OCR Intake Lite előkészítés

1. `ocr/pipeline.py`: input fájlok beolvasása.
2. `ocr/tesseract_engine.py`: OCR adapter.
3. `ocr/document_classifier.py`: szabályalapú dokumentumtípus-javaslat.
4. `ocr/metadata_extractor.py`: regex alapú metaadat-javaslat.
5. Ugyanazt a `CSClient`, `ReportGenerator` és audit szemléletet használja, mint a migrációs modul.

## RelatedWorkspace name resolution note

The optional `RelatedWorkspace` sheet can link Business Workspaces through the official Business Workspaces related items API. For maximum safety, prefer `target_node_id`, because it identifies the target Business Workspace unambiguously.

`target_workspace` and `source_workspace` may also contain an existing Business Workspace name. ContentFlow AI searches `GET /api/v2/businessworkspaces` with `expanded_view=true` and `where_name=<name>`, then accepts only an exact trimmed name match. If exactly one matching Business Workspace is found, its node ID is used. If no exact match is found, the row fails with `RELATED_SOURCE_NOT_FOUND` or `RELATED_TARGET_NOT_FOUND`. If multiple exact matches are found, the row is rejected with `RELATED_SOURCE_AMBIGUOUS` or `RELATED_TARGET_AMBIGUOUS`.

## Cleanup / rollback commands

Use cleanup only from a generated execution JSON report. The cleanup commands never search broad paths and only act on node IDs explicitly listed in the referenced report.

Plan first:

```powershell
python -m contentflow_ai.migration.cli cleanup-plan reports\<execution_report>.json --config config\config.local.json
```

Execute after review:

```powershell
python -m contentflow_ai.migration.cli cleanup-execute reports\<execution_report>.json --config config\config.local.json --yes
```

Safety notes:

- `cleanup-plan` is read-only and writes JSON/Markdown cleanup plan reports.
- `cleanup-execute` requires `--yes` and refuses to run if the execution report has no created workspace node IDs.
- Related Business Workspace relations are removed before created workspaces are deleted.
- Only workspace rows with original status `created` and a node ID are deleted.
- Related target workspace nodes are never deleted; cleanup only removes the relation from the source workspace.
- Uploaded files are listed as informational children and are expected to be removed with their created workspace when applicable.
