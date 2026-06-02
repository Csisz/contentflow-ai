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
