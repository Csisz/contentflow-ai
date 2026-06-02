# ContentFlow AI – rövid fejlesztési sprintekhez használható promptok

## Sprint 1 – Biztonságos repo és moduláris migrációs core

Dolgozz a `ContentFlow AI` projekten. A cél a régi egyfájlos OpenText Content Server importer moduláris Python package-be rendezése. Tartsd meg a meglévő működést, de válaszd szét a config, Excel parser, Content Server client, validator, reporter és import engine rétegeket. Ne kerüljön a repóba éles URL, jelszó, ügyféladat vagy migrációs XLSX. Minden titok `.env` vagy `config.local.json` fájlból jöjjön. A végén futtasd a unit teszteket, és írd le, melyik régi funkció melyik új modulba került.

## Sprint 2 – Migration Copilot preflight validator

Bővítsd a Migration Copilot preflight validátort. A rendszer ellenőrizze az Excel sheeteket, kötelező mezőket, üres title/location/src értékeket, duplikált workspace-eket, duplikált fájlokat célmappán belül, tiltott karaktereket, túl hosszú neveket, hiányzó lokális fájlokat, nulla bájtos fájlokat, MIME hint problémákat és kötelező category értékeket. Az eredmény egységes `Issue` modellbe kerüljön severity, code, row_type, row_index, field, message, suggestion és blocking mezőkkel. A végén legyen readiness score és GO / GO_WITH_WARNINGS / NO_GO döntés.

## Sprint 3 – Riportgenerálás és ügyfélbarát összefoglaló

Készíts riportgenerátort a preflight eredményből. Legyen JSON gépi feldolgozáshoz, Markdown vezetői/ügyfél összefoglalóhoz, CSV/XLSX hibajegy lista javításhoz. A Markdown riport tartalmazzon executive summaryt, readiness score-t, go/no-go döntést, hibaösszesítőt, részletes issue táblát és javasolt következő lépéseket. A riport ne tartalmazzon jelszót vagy titkos adatot.

## Sprint 4 – Read-only Content Server preflight

Bővítsd a Content Server preflight ellenőrzést read-only módban. A kliens csak autentikáljon, root node-ot ellenőrizzen, célpathokat próbáljon feloldani, és jelezze a hiányzó célmappákat warningként. Ne hozzon létre mappát, workspace-et, dokumentumot és ne módosítson kategóriát. Írj mockolt teszteket a sikeres path ellenőrzésre, hiányzó pathra és autentikációs hibára.

## Sprint 5 – OCR Intake Lite skeleton

Készítsd elő az OCR Intake Lite modult, de még ne keverd össze a migrációs importerrel. Legyen `ocr/pipeline.py`, `ocr/models.py`, `ocr/tesseract_engine.py`, `ocr/document_classifier.py` és `ocr/metadata_extractor.py`. Az első verzió input mappából PDF/TIFF/JPG/PNG fájlokat listázzon, OCR adapter interfészt definiáljon, szabályalapú dokumentumtípus-javaslatot adjon, metaadat-javaslatot készítsen regex alapján, és JSON audit rekordot generáljon. Content Serverbe csak későbbi jóváhagyott lépésben töltsön fel.

## Tesztsor minden sprint után

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q
python -m contentflow_ai.migration.cli analyze tests\fixtures\sample_migration.xlsx --config config\config.local.json
```

Ha nincs fixture Excel, először hozz létre szintetikus tesztfájlt, amelyben van legalább egy workspace sor, egy létező fájl sor és egy hibás/missing file sor. A teszt akkor jó, ha a pytest lefut, az analyze riport elkészül, és a hibás sorokra `MISSING_SOURCE_FILE` vagy más várt issue code jelenik meg.
