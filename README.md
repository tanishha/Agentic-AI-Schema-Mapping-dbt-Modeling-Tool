# DBMapper

DBMapper is a browser-based data migration tool for loading CSV and JSON source files into SQLite. It profiles uploaded files, parses or generates a target schema, uses an LLM to propose source-to-target mappings, lets the user review/edit mappings, then writes data into both a session database and a shared project database.

The backend is FastAPI + LangGraph. The UI is a single static HTML/JS file. LLM calls go through `llm_client.py`, which supports Azure AI Foundry or local Ollama through `.env` settings.

---

## Current Workflow

```text
Upload source files
  -> choose schema source
  -> profile CSV/JSON columns
  -> parse/validate schema
  -> select target tables
  -> infer mappings with LLM
  -> review/edit mappings
  -> migrate to SQLite
  -> validate + ERD + downloads
```

### Schema Source Options

The upload screen supports three modes:

| Mode | Use when | SQL upload required |
|---|---|---|
| Upload `target_schema.sql` | You already have the target schema file | Yes |
| Generate suggested schema | First load, schema does not exist yet | No |
| Use `project.db` schema | Loading new source files into the existing project DB schema | No |

Generated schemas are shown as editable SQL before continuing. The user can manually edit the DDL or use the feedback box to ask the LLM for changes, then click **Use Schema & Continue**.

Before mapping starts, DBMapper validates the schema by executing it in an in-memory SQLite database and checking FK references. Syntax errors, missing FK tables, and missing FK columns stop the flow with an error.

---

## Databases

This project uses SQLite files. There are no DB credentials, host, port, username, or password.

| Database | Path | Purpose |
|---|---|---|
| Session DB | `data/uploads/<session-id>/migration.db` | Output for one migration session |
| Project DB | `data/database/project.db` | Shared database across sessions |
| Checkpoint DB | `data/checkpoints.db` | LangGraph workflow state |

The server prints the active session folder in the terminal:

```text
[SESSION] created id=<uuid> upload_dir=C:\...\data\uploads\<uuid>
[SESSION] upload id=<uuid> upload_dir=C:\...\data\uploads\<uuid> files=[...]
[SESSION] start id=<uuid> schema_mode=generate upload_dir=C:\...\data\uploads\<uuid>
```

Use that UUID to inspect the matching folder under `data/uploads/`.

Session DBs are recreated for each run. `project.db` is persistent and uses `INSERT OR REPLACE` for primary-key conflicts.

---

## Setup

### 1. Create Environment

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

Or with `uv`:

```powershell
uv venv
.\.venv\Scripts\activate
uv pip install -r requirements.txt
```

### 2. Configure `.env`

Use Azure AI Foundry:

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

Or local Ollama:

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

Do not commit `.env`; it can contain API keys.

### 3. Start Server

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

Open:

```text
http://127.0.0.1:8000/
```

---

## UI Usage

1. Choose a schema mode.
2. Upload one or more `.csv` / `.json` source files.
3. If using uploaded schema mode, also upload one `.sql` file.
4. Click **Start Profiling**.
5. If generating schema, review/edit the generated DDL or ask for changes.
6. Click **Use Schema & Continue**.
7. Select target tables to populate.
8. Review mappings. Auto-generated integer primary keys can be skipped.
9. Click **Confirm & Migrate**.
10. Review row counts, validation, ERD, and downloads.

Downloads available:

- `migration.db`
- `report.json`
- `target_schema.sql`

---

## Inspecting Databases

Use the included Python runner if SQLite CLI is not installed:

```powershell
# Project DB table counts
.\venv\Scripts\python.exe data/database/run_sql.py data/database/scripts/table_counts.sql

# Specific session DB table counts
.\venv\Scripts\python.exe data/database/run_sql.py data/database/scripts/table_counts.sql --db data/uploads/<session-id>/migration.db

# Run a script by name from data/database/scripts
.\venv\Scripts\python.exe data/database/run_sql.py table_counts.sql --db project.db
```

With SQLite CLI:

```powershell
sqlite3 data/database/project.db ".tables"
sqlite3 data/database/project.db ".schema"
sqlite3 data/database/project.db < data/database/scripts/select_all.sql
```

Included scripts:

| Script | Purpose |
|---|---|
| `table_counts.sql` | Count rows per table |
| `select_all.sql` | Select from all known tables |
| `delete_all_data.sql` | Delete rows while keeping schema |
| `drop_all_tables.sql` | Drop all tables |

---

## Project Structure

```text
DBMapper/
|-- main.py                    # FastAPI routes, SSE, sessions
|-- graph.py                   # LangGraph workflow
|-- models.py                  # TypedDict state/schema models
|-- llm_client.py              # Azure/Ollama client factory
|-- agents/
|   |-- data_io.py             # CSV/JSON loading and JSON flattening
|   |-- file_profiler.py       # Source column profiling
|   |-- schema_generation.py   # AI schema generation and feedback refinement
|   |-- schema_review.py       # Generated-schema interrupt node
|   |-- ddl_parser.py          # SQL parsing + SQLite validation
|   |-- table_selection.py     # Table-selection interrupt node
|   |-- mapping_inference.py   # LLM mapping inference + sanitization
|   |-- human_review.py        # Human review interrupt node
|   |-- data_migration.py      # SQLite migration + validation
|-- static/
|   |-- index.html             # Single-page UI
|-- data/
|   |-- raw/                   # Sample files
|   |-- uploads/               # Per-session files, migration.db, report.json
|   |-- database/
|       |-- project.db          # Shared SQLite DB
|       |-- run_sql.py          # SQL runner
|       |-- scripts/            # Utility SQL scripts
```

---

## Notes

- `project.db` schema can be reused from the upload screen with **Use project.db schema**.
- Generated schemas should be reviewed before migration.
- If a generated table name is a reserved SQL word, rename it or quote it.
- Child foreign-key columns still need real source values unless a future lookup stage is added.
