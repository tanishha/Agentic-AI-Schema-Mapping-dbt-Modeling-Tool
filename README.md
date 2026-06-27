# DBMapper

DBMapper is a local agentic data-engineering workspace for profiling CSV/JSON files, designing or reusing a SQLite target schema, inferring source-to-target mappings with an LLM, loading data into SQLite, and generating dbt transformations for cleaned staging and mart models.

The backend is FastAPI + LangGraph. The UI is a single static HTML/CSS/JS app. LLM calls go through `llm_client.py`, which supports Azure AI Foundry or local Ollama through `.env` settings.

## End-to-End Workflow

```text
Upload CSV/JSON files
  -> profile source columns
  -> load raw/session tables
  -> choose uploaded/generated/project/extended target schema
  -> validate DDL syntax and FK semantics
  -> select target tables
  -> infer mappings with LLM
  -> human review/edit
  -> load session migration.db and shared project.db
  -> validate counts/FKs/NOT NULLs and render ERD
  -> prepare dbt project
  -> preview AI-generated transformations
  -> approve/apply dbt files
  -> run dbt debug/compile/build/test
```

## Agentic AI Role

DBMapper uses agents as task-specific reasoning units around deterministic data-engineering operations. The deterministic layer handles file IO, schema parsing, SQLite validation, database writes, and dbt execution. The agentic layer handles ambiguous design decisions: target schema generation, schema refinement from user feedback, source-to-target mapping inference, relationship-aware dbt planning, dbt SQL/model/test generation, and feedback-driven dbt edits.

Human approval is intentionally part of the loop. AI can propose schema DDL, mappings, joins, dbt models, tests, and transformation changes, but the user reviews before migration or dbt file application. This gives the project an agentic AI pattern without letting the LLM silently mutate databases or overwrite SQL.

## Schema Modes

| Mode | Use when | SQL upload required |
|---|---|---|
| Upload final schema SQL | You already have final target DDL | Yes |
| Generate final schema | First load and no schema exists yet | No |
| Use `project.db` final schema | Loading new files into existing tables | No |
| Extend `project.db` final schema | New files should add connected tables/columns | No |

Generated and extended schemas are shown as editable SQL. The user can manually edit the DDL or ask the schema feedback agent for changes before continuing.

Before mapping starts, DBMapper validates the schema by executing it in an in-memory SQLite database and checking foreign-key references. Syntax errors, missing FK tables, and missing FK columns block the flow.

## Databases

This project uses local SQLite files. There are no database credentials, hosts, ports, usernames, or passwords.

| Database | Path | Purpose |
|---|---|---|
| Session DB | `data/uploads/<session-id>/migration.db` | Per-session migration result |
| Project DB | `data/database/project.db` | Persistent shared database across sessions |
| Checkpoint DB | `data/checkpoints.db` | LangGraph workflow state |

The terminal prints the active session folder:

```text
[SESSION] created id=<uuid> upload_dir=C:\...\data\uploads\<uuid>
[SESSION] upload id=<uuid> upload_dir=C:\...\data\uploads\<uuid> files=[...]
[SESSION] start id=<uuid> schema_mode=generate upload_dir=C:\...\data\uploads\<uuid>
```

Session DBs are recreated per run. `project.db` is persistent and uses replacement behavior for primary-key conflicts.

## dbt Workflow

The dbt screen can be opened after a migration or directly from the upload page when `project.db` already contains tables.

Current dbt controls:

| Button | Purpose |
|---|---|
| Back to Upload | Return to upload workflow |
| Refresh Table List | Re-read current `project.db` tables and row counts only |
| Prepare dbt Project | Refresh dbt config/sources and create only missing starter files |
| Transformations | Ask AI for a dbt transformation preview; no files are written |
| Apply Transformations | Apply the reviewed preview, create a backup, and preserve existing SQL models |
| Apply dbt Feedback | Ask AI to update dbt files from user feedback; creates a backup first |
| dbt debug | Validate dbt adapter/profile/project configuration |
| dbt compile | Compile dbt graph and SQL without materializing models |
| dbt build | Run models and tests together |
| dbt test | Run dbt tests only |
| Run All dbt Steps | Run debug -> compile -> build -> test in sequence |

`Transformations` shows suggested file actions before apply:

- `create`: new dbt model file will be created.
- `update`: generated YAML/plan metadata will be refreshed.
- `preserve`: existing SQL model file will not be overwritten.

Generated dbt models live under `data/dbt/models/`. Custom/manual models can live under `data/dbt/models/custom/`; DBMapper does not overwrite that folder. Runtime dbt logs, target files, backups, `.user.yml`, and pending previews are ignored by git.

## Setup

Create a virtual environment:

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

Configure `.env` for Azure AI Foundry:

```ini
LLM_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/openai/v1/
AZURE_OPENAI_API_KEY=<your-api-key>
AZURE_OPENAI_DEPLOYMENT=gpt-4.1
AZURE_OPENAI_DEPLOYMENT_FAST=gpt-4.1

UPLOAD_DIR=data/uploads
CHECKPOINT_DB=data/checkpoints.db
MIGRATION_DB=data/database/project.db
PROFILER_SAMPLE_ROWS=1000
```

Or configure local Ollama:

```ini
LLM_PROVIDER=ollama
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:7b
LLM_MODEL_FAST=qwen3.5:4b
LLM_API_KEY=ollama

UPLOAD_DIR=data/uploads
CHECKPOINT_DB=data/checkpoints.db
MIGRATION_DB=data/database/project.db
PROFILER_SAMPLE_ROWS=1000
```

Start the server:

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

Open:

```text
http://127.0.0.1:8000/
```

## Inspect Databases

Use the included SQL runner:

```powershell
# Project DB table counts
.\venv\Scripts\python.exe data/database/run_sql.py data/database/scripts/table_counts.sql

# Specific session DB table counts
.\venv\Scripts\python.exe data/database/run_sql.py data/database/scripts/table_counts.sql --db data/uploads/<session-id>/migration.db

# dbt object counts in project.db
.\venv\Scripts\python.exe data/database/run_sql.py data/database/scripts/dbt_table_counts.sql --db data/database/project.db
```

Included scripts:

| Script | Purpose |
|---|---|
| `table_counts.sql` | Count loaded/source, dbt staging, and dbt mart tables |
| `source_table_counts.sql` | Count DBMapper-loaded source/final tables only |
| `dbt_table_counts.sql` | Count dbt staging and mart objects only |
| `select_all.sql` | Select from known tables |
| `delete_all_data.sql` | Delete rows while keeping schema |
| `drop_all_tables.sql` | Drop all tables |

## Project Structure

```text
DBMapper/
|-- main.py                    # FastAPI routes, SSE, sessions, dbt endpoints
|-- graph.py                   # LangGraph workflow
|-- models.py                  # TypedDict state/schema models
|-- llm_client.py              # Azure/Ollama client factory
|-- agents/
|   |-- data_io.py             # CSV/JSON loading and JSON flattening
|   |-- file_profiler.py       # Source column profiling
|   |-- raw_loader.py          # Automatic raw/session table load
|   |-- schema_generation.py   # AI schema generation and feedback refinement
|   |-- schema_review.py       # Generated-schema interrupt node
|   |-- ddl_parser.py          # SQL parsing + SQLite validation
|   |-- table_selection.py     # Table-selection interrupt node
|   |-- mapping_inference.py   # LLM mapping inference + sanitization
|   |-- human_review.py        # Human review interrupt node
|   |-- data_migration.py      # SQLite migration + validation
|   |-- dbt_generation.py      # AI dbt plan/model/test preview and apply
|   |-- dbt_runner.py          # dbt command execution
|-- static/
|   |-- index.html             # Single-page UI
|-- data/
|   |-- raw/                   # Sample files
|   |-- uploads/               # Per-session files, migration.db, report.json
|   |-- dbt/                   # dbt scaffold, models, plan
|   |-- database/
|       |-- project.db          # Shared SQLite DB
|       |-- run_sql.py          # SQL runner
|       |-- scripts/            # Utility SQL scripts
```

## Notes

- `project.db` schema can be reused with **Use project.db final schema**.
- New connected tables/columns can be proposed with **Extend project.db schema**.
- Generated schemas and dbt transformations should be reviewed before applying.
- dbt mart tables use a `mart_` prefix so they do not overwrite ingestion tables.
- `data/dbt/logs/dbt.log` is expected to be verbose and append-heavy; it is dbt runtime output.
