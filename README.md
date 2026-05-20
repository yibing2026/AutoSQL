# Data Import Agent

It provides a small FastAPI service that can:

- import a single local workbook directly into PostgreSQL
- chat with an OpenAI-backed workbook assistant to preview and apply natural-language table edits
- discover a cached FDZS summary workbook and import it into PostgreSQL
- discover a downloaded data directory and import summary rows plus annotation files
- support `dry_run` previews before writing to the database
- save each import run to local SQLite history

## Project layout

```text
app/
  api/
    routes.py
  core/
    config.py
    database.py
  repositories/
    import_history_repo.py
  schemas/
    data_import.py
    system.py
  services/
    data_import_agent.py
    import_jobs.py
data/
  sample_import_request.json
scripts/
  smoke_test.py
.env.example
README.md
requirements.txt
```

## Quick start

```powershell
cd "D:\AutoSQL"
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# edit .env and set OPENAI_API_KEY if you want to use workbook chat
uvicorn app.main:app --reload
```

Open:

- [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- [http://127.0.0.1:8000/](http://127.0.0.1:8000/) -> redirects to `/docs`
- [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- [http://127.0.0.1:8000/api/v1/status](http://127.0.0.1:8000/api/v1/status)

## Main endpoints

### `POST /api/v1/imports/run`

Run the import agent.

Example request:

```json
{
  "mode": "auto",
  "dry_run": true
}
```

Optional fields:

- `target_database_name`: optional PostgreSQL database name for this run
- `workbook_source`: explicit path to a local workbook file
- `workbook_table_prefix`: table prefix used when importing sheets from a workbook
- `cached_summary_source`: explicitly point to the cached summary workbook
- `downloaded_root`: explicitly point to the downloaded data directory

Modes:

- `workbook_file`: import one local workbook directly; common workbooks are loaded per sheet, and the Anhui Shengli template is parsed into linked clinical/statistics tables
- for recognized hospital templates, the service can also build unified medical-schema tables for cross-hospital expansion
- `auto`: try both import paths
- `cached_summary`: only run the cached MQ summary import
- `downloaded_data`: only run the downloaded directory import

PowerShell example for a local workbook:

```powershell
$body = @{
  mode = "workbook_file"
  dry_run = $true
  target_database_name = ""
  workbook_source = "D:\2026_02_28_07 成都公卫\04 安徽省立医院\1.受试者临床诊疗信息收集表-安徽省立-信息大表20260228.xlsx"
  workbook_table_prefix = "ahs"
} | ConvertTo-Json -Compress

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/v1/imports/run" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body $body
```

When `dry_run = $false`, the agent will really write the workbook data into PostgreSQL.

### `POST /api/v1/imports/upload`

Upload a local workbook directly to the API as `multipart/form-data`.

PowerShell example:

```powershell
$form = @{
  file = Get-Item "D:\2026_02_28_07 成都公卫\04 安徽省立医院\1.受试者临床诊疗信息收集表-安徽省立-信息大表20260228.xlsx"
  dry_run = "true"
  workbook_table_prefix = "ahs"
  target_database_name = ""
}

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/v1/imports/upload" `
  -Method Post `
  -Form $form
```

在 Swagger 页面里也可以直接用这个上传接口选文件测试。

### `GET /api/v1/imports/history`

Read recent import runs stored in SQLite.

### `GET /api/v1/status`

Read the current service configuration summary.

### `POST /api/v1/workbook-chat/upload`

Upload a workbook, describe the desired edit in natural language, let the OpenAI planner generate a structured edit plan, and apply the edit with Pandas.

Typical use cases:

- rename or drop columns
- standardize null-like values such as `/`, `N/A`, `null`
- convert age columns such as `29岁` or `1岁9天` into numeric values
- replace inconsistent labels with a normalized value set

Example workflow in Swagger:

1. Open `/docs`
2. Find `POST /api/v1/workbook-chat/upload`
3. Upload an `.xlsx`, `.xls`, or `.csv` file
4. Enter an instruction such as `把年龄列统一转成数字，并把 / 处理成空值`
5. Review `plan`, `preview_before`, `preview_after`, and `edited_workbook_path`

### `POST /api/v1/workbook-chat/path`

Continue editing a local workbook by path, which is useful for multi-round refinement after the upload endpoint returns an `edited_workbook_path`.

## Smoke test

```powershell
.\.venv\Scripts\python .\scripts\smoke_test.py
```

The smoke test verifies:

- `/health`
- `/api/v1/status`
- `/api/v1/imports/run`
- `/api/v1/imports/history`

## Anhui Shengli template output

When the workbook matches the Anhui Shengli clinical template, the agent uses a specialized parser and creates these short linked tables:

- `ahs_pt`: patient-level base table
- `ahs_csf_rtn`: cerebrospinal fluid routine items
- `ahs_bio`: biochemistry items
- `ahs_csf_cul`: cerebrospinal fluid culture items
- `ahs_smear`: smear items
- `ahs_imm`: host immune items
- `ahs_micro`: microbial culture or pathogen-related items
- `ahs_other`: other detection-method items
- `ahs_img`: imaging items

Shared join keys across all detail tables:

- `patient_id`

Retained business fields in the patient base table:

- `patient_seq`
- `encounter_id`
- `sample_id`
- `screening_no`
- `inpatient_no`
- `lab_no`
- `sample_no`

Additional parser cleanup for the Anhui template:

- patient age is normalized to numeric `age`
- patient name and recorder name are removed
- slash-only values such as `/` are normalized to null
- a built-in quality check adds warnings to the import notes for shifted or suspicious rows

## Unified medical schema

The project now starts to expose a cross-hospital standard schema alongside hospital-specific tables.

Current standard tables:

- `ahs_std_patient`
- `ahs_std_encounter`
- `ahs_std_sample`
- `ahs_std_lab_item`
- `ahs_std_culture_item`
- `ahs_std_imaging_item`
- `ahs_std_import_issue`

Current issue-audit behavior:

- template-specific parser warnings are also materialized into `*_issue_audit`
- those warnings are then mapped into `*_std_import_issue`
- this lets downstream systems query quality issues as rows instead of only reading free-text notes

Design goal:

- use hospital-specific parsers only for source understanding
- map parsed results into a shared medical statistics schema
- let future hospitals reuse the same downstream query model instead of creating a new database design every time

Current generic recognizer behavior:

- non-Anhui workbooks now keep their raw per-sheet tables and also generate `*_std_*` tables
- if a worksheet is a merged clinical template with many `Unnamed:` columns, the recognizer will try to rebuild headers from in-sheet layout rows before extracting patient/sample/lab/imaging data
- if key identity fields still cannot be recognized, the agent writes an `id_fields_not_recognized` row into `*_std_import_issue`

Automatic quality review:

- `/api/v1/imports/run` accepts `review_with_ai` and `review_sample_rows`
- `/api/v1/imports/upload` accepts the same two form fields
- when enabled, the import response includes `quality_review`
- the review first runs local rule-based checks, then adds an OpenAI summary when `OPENAI_API_KEY` is configured
- current checks focus on non-numeric age, suspicious date columns, numeric pollution in `test_group`, empty results after `:` and whether units / reference ranges seem absent from `item_result`

## Notes

- SQLite is only used for local run history
- PostgreSQL is the target business database; workbook imports create a new database by default unless you set `target_database_name`
- `dry_run=true` is the safest way to verify source discovery before a real import
- workbook chat requires `OPENAI_API_KEY` and the `openai` Python package from `requirements.txt`
- the current workbook chat flow is intentionally constrained: the model plans edits, and the backend applies only supported deterministic actions
