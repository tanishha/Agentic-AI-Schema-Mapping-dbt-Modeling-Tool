# PRD: DBMapper Agentic Schema Migration Tool

**Version:** 1.5  
**Date:** 2026-06-26  
**Status:** Implemented / evolving

---

## 1. Summary

DBMapper is a local browser-based migration tool for loading CSV and JSON source files into SQLite. It supports three target-schema paths:

1. Upload an existing `target_schema.sql`.
2. Generate a suggested schema from uploaded source files.
3. Reuse the current schema from `data/database/project.db`.

The system profiles source files, parses and validates the target schema, lets the user select target tables, asks an LLM to infer source-to-target mappings, lets the user edit mappings, and writes data into SQLite.

The main components are:

- FastAPI server for routes, SSE, uploads, downloads, and session management.
- LangGraph workflow for profiling, schema generation/parsing, mapping, and migration stages.
- Plain HTML/CSS/JS UI in `static/index.html`.
- SQLite databases for session output, project output, and graph checkpoints.
- LLM provider abstraction in `llm_client.py` supporting Azure AI Foundry and Ollama.

---

## 2. Goals

| ID | Goal |
|---|---|
| G1 | Accept one or more CSV/JSON source files per migration session |
| G2 | Support uploaded, generated, and project DB schema modes |
| G3 | Profile source columns before LLM mapping |
| G4 | Validate schema syntax and FK references before mapping |
| G5 | Let the user select target tables before mapping |
| G6 | Infer mappings with confidence and reason text |
| G7 | Let users edit mappings before data is loaded |
| G8 | Write to a session-specific `migration.db` and shared `project.db` |
| G9 | Show row counts, validation, ERD, and downloadable outputs |
| G10 | Print session folder paths in the terminal for easier debugging |

### Out of Scope

- Authentication and multi-tenant access control
- Cloud database targets
- Automatic lookup-table key propagation for synthetic parent IDs
- Scheduled/recurring batch loads
- Large-file chunked migration

---

## 3. User Flow

### Step 1: Choose Schema Source and Upload

The UI asks the user to choose one schema mode:

| Mode | Description | Required files |
|---|---|---|
| Upload `target_schema.sql` | User supplies existing target DDL | CSV/JSON + one SQL file |
| Generate suggested schema | AI generates SQLite DDL from source profiles | CSV/JSON only |
| Use `project.db` schema | Server extracts DDL from existing project DB | CSV/JSON only |

### Step 2: Profile Source Files

`FileProfilerAgent` loads CSV/JSON files through `agents/data_io.py`, flattens JSON where needed, and produces an intermediate catalog with:

- source file
- column name
- inferred type
- null percentage
- distinct count
- sample values

### Step 3: Schema Generation or Parsing

For uploaded SQL and project DB schema modes:

- DDL is parsed by `DDLParserAgent`.

For generated mode:

- `SchemaGenerationAgent` creates SQLite DDL from the source catalog.
- The user can edit the DDL directly.
- The user can provide feedback to regenerate/refine the schema.
- The user can download the draft schema before continuing.

### Step 4: Schema Validation

Before table selection:

- DDL is executed in an in-memory SQLite database.
- FK references are checked against parsed table/column names.
- Syntax or semantic errors block progression.

Examples:

- Unquoted reserved table names like `order` fail.
- FK references to missing tables fail.
- FK references to missing columns fail.

### Step 5: Table Selection

The user selects one or more target tables to populate.

### Step 6: Mapping Inference

`MappingInferenceAgent` sends the selected target schema and source catalog to the LLM. It returns one mapping row per target column.

Rules:

- Source file/column must exist in the profiled catalog.
- Invented source columns are sanitized to `null`.
- Auto-generated integer primary keys can be skipped.
- Required non-PK / FK columns must have mappings before migration.

### Step 7: Human Review

The user reviews mappings table-by-table.

`Confirm & Migrate` stays clickable. If required mappings are missing, the UI displays a reason instead of silently disabling the button.

### Step 8: Migration

`DataMigrationAgent`:

- creates/recreates session DB tables
- creates missing project DB tables
- applies confirmed mappings
- inserts transformed rows
- validates FK and NOT NULL constraints
- writes `report.json`

### Step 9: Done

The UI shows:

- row counts by table/file
- migration status and reason
- validation cards
- ERD diagram
- downloads for `migration.db`, `report.json`, and `target_schema.sql`

---

## 4. Architecture

```text
Browser UI
  | upload files
  | SSE progress
  | REST actions
  v
FastAPI server
  | manages sessions and upload folders
  | invokes LangGraph / direct stage helpers
  v
LangGraph workflow
  | file_profiler
  | schema_generation? / ddl_parser
  | table_selection interrupt
  | mapping_inference
  | human_review interrupt
  | data_migration
  v
SQLite outputs
  | data/uploads/<session-id>/migration.db
  | data/database/project.db
  | data/checkpoints.db
```

---

## 5. LangGraph Flow

Current graph:

```text
file_profiler
  -> conditional:
       schema_mode=generate -> schema_generation -> schema_review -> ddl_parser
       schema_mode=upload/project -> ddl_parser
  -> table_selection
  -> mapping_inference
  -> human_review
  -> data_migration
```

Interrupt points:

- `schema_review` for generated schemas
- `table_selection`
- `human_review`

Some transitions are handled directly by FastAPI helper functions instead of full graph resume to avoid stale SSE event replay.

---

## 6. API Design

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Serve UI |
| `POST` | `/sessions` | Create session and upload folder |
| `POST` | `/sessions/{id}/upload` | Upload source/schema files |
| `POST` | `/sessions/{id}/start?schema_mode=...` | Start profiling/schema flow |
| `GET` | `/sessions/{id}/events` | SSE progress stream |
| `GET` | `/sessions/{id}/schema-draft` | Return current DDL draft |
| `POST` | `/sessions/{id}/schema-feedback` | Refine DDL using user feedback |
| `POST` | `/sessions/{id}/confirm-schema` | Validate/parse edited DDL |
| `GET` | `/sessions/{id}/schema` | Return parsed target schema |
| `GET` | `/sessions/{id}/catalog` | Return source profile catalog |
| `POST` | `/sessions/{id}/select-tables` | Select target tables and infer mappings |
| `GET` | `/sessions/{id}/mappings` | Return proposed mappings |
| `PUT` | `/sessions/{id}/mappings/{mapping_id}` | Edit one mapping |
| `POST` | `/sessions/{id}/confirm` | Run migration |
| `POST` | `/sessions/{id}/remigrate` | Rerun migration after editing mappings |
| `GET` | `/sessions/{id}/validate` | Validate session DB |
| `GET` | `/sessions/{id}/result` | Return migration result summary |
| `GET` | `/sessions/{id}/download/db` | Download `migration.db` |
| `GET` | `/sessions/{id}/download/report` | Download `report.json` |
| `GET` | `/sessions/{id}/download/schema` | Download current session schema |
| `GET` | `/database/download/schema` | Download `project.db` schema |
| `POST` | `/sessions/{id}/reset` | Delete session upload folder |

### SSE Event Types

```json
{"type": "profiling_progress", "payload": {"file": "source.csv", "columns": 6}}
{"type": "schema_generated", "payload": {"source": "llm", "ddl": "..."}}
{"type": "ddl_parsed", "payload": {"tables": [{"name": "customer", "columns": 5}]}}
{"type": "mapping_ready", "payload": {"mappings": []}}
{"type": "migration_progress", "payload": {"table": "customer", "file": "source.csv", "rows": 100}}
{"type": "done", "payload": {"total_rows": 100}}
{"type": "error", "payload": {"message": "..."}}
{"type": "stream_end"}
```

---

## 7. Data and Storage

### Session Upload Folder

Path:

```text
data/uploads/<session-id>/
```

Contents:

```text
<source files>
target_schema.sql          # only if uploaded by user
migration.db               # created after migration
report.json                # created after migration
```

The terminal logs the session folder:

```text
[SESSION] created id=<uuid> upload_dir=C:\...\data\uploads\<uuid>
[SESSION] upload id=<uuid> upload_dir=C:\...\data\uploads\<uuid> files=[...]
[SESSION] start id=<uuid> schema_mode=project upload_dir=C:\...\data\uploads\<uuid>
```

### Database Behavior

| DB | Path | Lifecycle | Insert behavior |
|---|---|---|---|
| Session DB | `data/uploads/<session-id>/migration.db` | Recreated per run | `INSERT` |
| Project DB | `data/database/project.db` | Persistent | `INSERT OR REPLACE` |
| Checkpoint DB | `data/checkpoints.db` | Persistent graph state | LangGraph-managed |

There are no DB credentials because SQLite uses local files.

---

## 8. Agent Responsibilities

### `agents/data_io.py`

Shared CSV/JSON loader. Handles normal JSON, JSONL, nested JSON flattening, BOM-tolerant UTF-8, and display serialization for dict/list values.

### `agents/file_profiler.py`

Builds source catalog from uploaded files. No LLM call.

### `agents/schema_generation.py`

Generates SQLite DDL from source profiles. Supports user feedback refinement. Falls back to a conservative file/table-based schema when LLM is unavailable.

### `agents/ddl_parser.py`

Parses DDL using `sqlglot`, extracts:

- tables
- columns
- SQL types
- nullable flags
- primary keys
- inline and table-level foreign keys

Then validates the DDL in SQLite and checks FK references.

### `agents/mapping_inference.py`

Uses LLM to infer mappings. Sanitizes invented source columns. Allows synthetic integer primary keys to stay unmapped.

### `agents/data_migration.py`

Loads transformed data into session DB and project DB, validates, emits progress, and writes `report.json`.

---

## 9. Database Scripts

Location:

```text
data/database/scripts/
```

| Script | Purpose |
|---|---|
| `table_counts.sql` | Count rows |
| `select_all.sql` | Select data |
| `delete_all_data.sql` | Delete rows, keep schema |
| `drop_all_tables.sql` | Drop schema |

Run with:

```powershell
.\venv\Scripts\python.exe data/database/run_sql.py data/database/scripts/table_counts.sql
.\venv\Scripts\python.exe data/database/run_sql.py table_counts.sql --db project.db
.\venv\Scripts\python.exe data/database/run_sql.py table_counts.sql --db data/uploads/<session-id>/migration.db
```

---

## 10. Environment Variables

| Variable | Description |
|---|---|
| `LLM_PROVIDER` | `azure` or `ollama` |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI-compatible endpoint |
| `AZURE_OPENAI_API_KEY` | Azure API key |
| `AZURE_OPENAI_DEPLOYMENT` | Main model deployment |
| `AZURE_OPENAI_DEPLOYMENT_FAST` | Fast/fallback model deployment |
| `LLM_BASE_URL` | Ollama/OpenAI-compatible URL |
| `LLM_MODEL` | Main Ollama model |
| `LLM_MODEL_FAST` | Fast Ollama model |
| `LLM_API_KEY` | API key value for OpenAI SDK |
| `UPLOAD_DIR` | Upload root, default `data/uploads` |
| `CHECKPOINT_DB` | LangGraph checkpoint DB |
| `MIGRATION_DB` | Project DB path |
| `PROFILER_SAMPLE_ROWS` | Max rows sampled by profiler |

---

## 11. Functional Requirements

| ID | Requirement |
|---|---|
| FR-01 | System must accept one or more CSV/JSON source files |
| FR-02 | System must support uploaded, generated, and project DB schema modes |
| FR-03 | Generated schemas must be editable before mapping |
| FR-04 | User must be able to refine generated schema with feedback |
| FR-05 | Schema must be validated before table selection |
| FR-06 | User must be able to select target tables |
| FR-07 | Mapping inference must only run for selected tables |
| FR-08 | User must be able to edit mappings |
| FR-09 | Confirm action must show reasons when required mappings are missing |
| FR-10 | Auto-generated integer primary keys may be skipped |
| FR-11 | Session DB and project DB must both be written |
| FR-12 | DB, report, and schema must be downloadable |
| FR-13 | Terminal must log active session upload folder |
| FR-14 | ERD must show tables, FK lines, and cardinality |

---

## 12. Open Questions / Future Work

1. Add lookup-stage migration for synthetic parent IDs and child FKs.
2. Add chunked loading for large files.
3. Add session cleanup / TTL.
4. Add richer transformation expressions beyond simple `df.eval`.
5. Add authentication if deployed outside local development.
