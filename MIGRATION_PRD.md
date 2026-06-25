# PRD: Agentic Schema Migration Tool

**Version:** 1.2  
**Date:** 2026-06-25  
**Status:** Implemented

---

## 1. Executive Summary

A browser-based tool that accepts arbitrary CSV and JSON source files plus a target SQL DDL file, uses a LangGraph multi-agent pipeline to profile the sources, infer column mappings with confidence scores, present them in an editable review table, and — once the user confirms — migrates all data into SQLite databases matching the target schema.

The system is built on three components: a **LangGraph** agent graph for all AI-driven stages, a **FastAPI** server for the HTTP + Server-Sent Events layer, and a **plain HTML/JS** single-page UI. LLM inference is powered by **Azure AI Foundry** (default) with **Ollama** (local) as an alternative — both exposed through a shared `llm_client.py` factory. Switching providers requires only a `.env` change, no code changes.

---

## 2. Goals

| ID | Goal |
|---|---|
| G1 | Accept any number of CSV and JSON files alongside a SQL DDL file in a single upload step |
| G2 | Profile all source files and build a unified intermediate column catalog before any LLM call |
| G3 | Allow the user to select which tables in the DDL to target before mapping inference runs |
| G4 | Infer column mappings (source → target) with a confidence score and human-readable reason for each |
| G5 | Present mappings in an editable table — the human is always in control before any data moves |
| G6 | Execute the confirmed mappings into both a session-specific SQLite DB and a shared project DB |
| G7 | Stream progress to the UI via SSE so the user can see which stage the pipeline is in without polling |
| G8 | Support Azure AI Foundry and Ollama as interchangeable LLM backends |

### Out of scope (v1)

- Authentication / multi-tenancy
- Cloud database targets (Postgres, MySQL, etc.)
- Complex SQL expressions or multi-column derived fields in mappings
- Scheduling or recurring batch loads

---

## 3. High-Level Architecture

```
Browser (HTML/JS)
      │  multipart upload (files + DDL)
      │  SSE stream (progress events)
      │  REST (table selection, mappings, confirm)
      ▼
FastAPI Server
      │  creates LangGraph thread per session
      │  streams graph events → SSE
      ▼
LangGraph StateGraph  ──────────────────────────────────────────────
                                                                    │
  [FileProfilerAgent]                                               │
       │ builds intermediate catalog                                │
       ▼                                                            │
  [DDLParserAgent]                                                  │
       │ extracts target schema                                     │
       ▼                                                            │
  [TableSelectionNode]  ◄── interrupt_before ─── user picks tables  │
       │                                                            │
       ▼                                                            │
  [MappingInferenceAgent]  ◄── LLM via llm_client.py               │
       │ returns mappings + confidence                              │
       ▼                                                            │
  [HumanReviewNode]  ◄── interrupt_before ──── user edits / confirm │
       │                                                            │
       ▼                                                            │
  [DataMigrationAgent]                                              │
       │ creates SQLite tables from DDL                             │
       │ transforms + inserts rows                                  │
       │ writes to session DB + project DB                          │
       ▼                                                            │
     DONE                                                           │
─────────────────────────────────────────────────────────────────────
```

**LangGraph** owns all graph state and checkpointing.  
**FastAPI** is a thin adapter: it receives HTTP requests, forwards them into the graph, and pushes graph events back to the browser over SSE.  
**SQLite** serves three roles: graph checkpoints (`data/checkpoints.db`), per-session migration output (`data/uploads/<id>/migration.db`), and the shared project database (`data/database/project.db`).

---

## 4. LLM Provider Architecture

### 4.1 llm_client.py — Shared Factory

All LLM calls route through `llm_client.py`. It reads `LLM_PROVIDER` from the environment and returns a correctly configured `openai.OpenAI` client:

```python
def get_llm_client() -> OpenAI:
    provider = os.getenv("LLM_PROVIDER", "ollama")
    if provider == "azure":
        return OpenAI(
            base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
        )
    return OpenAI(
        base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.getenv("LLM_API_KEY", "ollama"),
    )

def get_model_name(fast: bool = False) -> str:
    provider = os.getenv("LLM_PROVIDER", "ollama")
    if provider == "azure":
        var = "AZURE_OPENAI_DEPLOYMENT_FAST" if fast else "AZURE_OPENAI_DEPLOYMENT"
    else:
        var = "LLM_MODEL_FAST" if fast else "LLM_MODEL"
    value = os.getenv(var)
    if not value:
        raise RuntimeError(f"Missing required env var: {var}")
    return value
```

Azure AI Foundry exposes an OpenAI-compatible endpoint (`/openai/v1/`), so the standard `openai.OpenAI` client works without the `AzureOpenAI` variant.

### 4.2 Provider Comparison

| Feature | Azure AI Foundry | Ollama (local) |
|---|---|---|
| `LLM_PROVIDER` value | `azure` | `ollama` |
| Auth | API key via `AZURE_OPENAI_API_KEY` | `LLM_API_KEY=ollama` |
| Endpoint | `AZURE_OPENAI_ENDPOINT` | `LLM_BASE_URL` |
| Model env var | `AZURE_OPENAI_DEPLOYMENT` | `LLM_MODEL` |
| Fast model env var | `AZURE_OPENAI_DEPLOYMENT_FAST` | `LLM_MODEL_FAST` |
| Deployed model (current) | `gpt-4.1` | `qwen2.5:7b` / `qwen3.5:4b` |
| Internet required | Yes | No |

---

## 5. User Flow

### Step 1 — Upload

- User drops any number of `.csv` or `.json` data files and exactly one `.sql` DDL file.
- Clicking **Start Profiling** triggers `POST /sessions/{id}/start`.
- Button label changes to "Profiling…" with a spinner.

### Step 2 — Profiling & DDL Parse (automated)

Progress events pushed via SSE as each file is processed. No user action required.

```
✅ crm_export.csv      profiled (8 columns)
✅ legacy_users.json   profiled (11 columns)
✅ target_schema.sql   parsed → tables: customers, contact_details, addresses
```

### Step 3 — Table Selection (human in the loop)

After DDL parsing the graph pauses (`interrupt_before=["table_selection"]`). The UI presents all tables extracted from the DDL as checkboxes. The user selects which tables to include in this migration run and clicks **Infer Mappings**.

`POST /sessions/{id}/select-tables` resumes the graph with the selected table names.

### Step 4 — Mapping Inference (automated, LLM)

The MappingInferenceAgent is called only for the selected tables. Proposed mappings are streamed back as a `mapping_ready` SSE event.

### Step 5 — Mapping Review (human in the loop)

The graph pauses again (`interrupt_before=["human_review"]`). The browser renders the review table per selected table with editable dropdowns and confidence bars. Clicking **Confirm & Migrate** sends `POST /sessions/{id}/confirm`.

### Step 6 — Migration

Progress events streamed per table × source file.

```
✅ [customers] crm_export.csv     → 200 rows
✅ [customers] legacy_users.json  → 150 rows
✅ [addresses] crm_export.csv     → 200 rows
...
Done — 900 total rows migrated
```

### Step 7 — Done

- Row count summary table
- FK / NOT NULL validation results per table
- ERD diagram
- Download `migration.db` and `report.json`
- **Edit Mappings** — returns to the review panel for re-migration without re-uploading

---

## 6. Agent Decomposition

### 6.1 Shared State — models.py

```python
class MigrationState(TypedDict):
    session_id: str
    stage: Literal["UPLOADING", "PROFILING", "READY", "MAPPING",
                   "REVIEWING", "MIGRATING", "DONE", "ERROR"]
    source_files: List[str]
    ddl_content: str
    intermediate_catalog: List[ColumnProfile]
    target_tables: List[TableSchema]
    selected_tables: List[str]          # populated by TableSelectionNode
    proposed_mappings: List[MappingItem]
    confirmed_mappings: List[MappingItem]
    rows_loaded: dict
    validation_results: List[ValidationResult]
    error: Optional[str]
    events: List[dict]
```

---

### 6.2 FileProfilerAgent

**File:** `agents/file_profiler.py`  
**Responsibility:** Read every source file, compute per-column statistics, build the unified intermediate catalog.

**Inputs:** `source_files`, `session_id`  
**Outputs:** `intermediate_catalog`, `stage = "READY"`

**Logic:**
- For each file, detect format from extension (`.csv` → pandas `read_csv`, `.json` → pandas `read_json` with orient auto-detection).
- Compute per column: `inferred_type`, `null_pct`, `distinct_count`, `sample_values` (up to 5 non-null values).
- Append a `profiling_progress` event to `state["events"]` after each file.
- No LLM call; pure pandas computation.
- Samples up to `PROFILER_SAMPLE_ROWS` rows (default 1000).

---

### 6.3 DDLParserAgent

**File:** `agents/ddl_parser.py`  
**Responsibility:** Parse the SQL DDL string into a structured target schema.

**Inputs:** `ddl_content`  
**Outputs:** `target_tables`, `stage = "MAPPING"`

**Logic:**
- Parse DDL with `sqlglot` (no LLM required for well-formed DDL).
- Extract table names, column names, SQL types, `NOT NULL` constraints, `PRIMARY KEY` markers, and inline `REFERENCES` foreign keys.
- If `sqlglot` fails to parse, fall back to an LLM call (`get_model_name(fast=True)`) to extract the schema as structured JSON.
- Emit a `ddl_parsed` event listing all tables and column counts.

---

### 6.4 TableSelectionNode *(interrupt point)*

**File:** `agents/table_selection.py`  
**Responsibility:** Pause the graph so the user can choose which tables from the DDL to include in this migration run.

**Mechanism:** `interrupt_before=["table_selection"]`. The browser shows the full list of parsed tables as a multi-select. When the user submits, `POST /sessions/{id}/select-tables` calls `_do_mapping_and_enqueue()` which runs `mapping_inference_agent` directly (bypassing the graph resume) with `state["selected_tables"]` populated.

**No LLM call.** The node itself just sets `stage = "MAPPING"`.

---

### 6.5 MappingInferenceAgent

**File:** `agents/mapping_inference.py`  
**Responsibility:** Call the LLM to propose a mapping from the intermediate catalog to the **selected** target tables only.

**Inputs:** `intermediate_catalog`, `target_tables`, `selected_tables`  
**Outputs:** `proposed_mappings`, `stage = "REVIEWING"`

**Logic:**
- Filter `target_tables` to only the selected ones.
- Build a prompt with the filtered schema JSON and the full source column catalog.
- Call the LLM via `get_llm_client()` / `get_model_name()` (provider-agnostic).
- Parse the JSON array response into `MappingItem` objects with stable UUIDs.
- Retry once on `JSONDecodeError`.
- Emit a `mapping_ready` event.

**Prompt rules enforced:**
- `confidence` is a float 0.0–1.0
- If no source maps, `source_file` and `source_column` are null, `confidence` is 0.0
- FK columns map to the same source as the PK in the referenced table
- `transformation` is null unless a concat or cast is needed

---

### 6.6 HumanReviewNode *(interrupt point)*

**File:** `agents/human_review.py`  
**Responsibility:** Pause the graph before migration; the user reviews and edits proposed mappings.

**Mechanism:** `interrupt_before=["human_review"]`. When `POST /sessions/{id}/confirm` arrives, `_do_migrate_and_enqueue()` runs `data_migration_agent` directly with confirmed mappings and updates the graph state.

**No LLM call.** The node sets `stage = "MIGRATING"`.

---

### 6.7 DataMigrationAgent

**File:** `agents/data_migration.py`  
**Responsibility:** Create SQLite target tables, transform source columns, insert rows, validate, and save a report. Writes to both the session DB and the shared project DB.

**Inputs:** `confirmed_mappings`, `source_files`, `target_tables`, `selected_tables`, `ddl_content`  
**Outputs:** `rows_loaded`, `validation_results`, `stage = "DONE"`

**Logic:**
1. **Session DB** (`data/uploads/<id>/migration.db`): Drop + recreate tables from DDL verbatim (`executescript(ddl)`), then insert all rows. `recreate=True`, `replace=False`.
2. **Project DB** (`data/database/project.db`): Use `CREATE TABLE IF NOT EXISTS` (no drop), then `INSERT OR REPLACE` on PK conflicts. `recreate=False`, `replace=True`.
3. For each selected table × source file:
   - Load source into a DataFrame via `_load_source_file()`.
   - Apply confirmed mappings via `_apply_mappings()` — renames columns, evaluates `transformation` expressions via `df.eval()`.
   - Use savepoints for per-file rollback safety.
   - Emit a `migration_progress` SSE event per file.
4. Validate via `validate_db()`: `PRAGMA foreign_key_check` + null counts on NOT NULL columns.
5. Save `report.json` to the session directory.
6. Emit a `done` event with total row count.

---

## 7. Graph Wiring

```python
builder = StateGraph(MigrationState)

builder.add_node("file_profiler",     file_profiler_agent)
builder.add_node("ddl_parser",        ddl_parser_agent)
builder.add_node("table_selection",   table_selection_node)
builder.add_node("mapping_inference", mapping_inference_agent)
builder.add_node("human_review",      human_review_node)
builder.add_node("data_migration",    data_migration_agent)

builder.set_entry_point("file_profiler")
builder.add_edge("file_profiler",     "ddl_parser")
builder.add_edge("ddl_parser",        "table_selection")
builder.add_edge("table_selection",   "mapping_inference")
builder.add_edge("mapping_inference", "human_review")
builder.add_edge("human_review",      "data_migration")
builder.add_edge("data_migration",    END)

compiled = builder.compile(
    checkpointer=SqliteSaver(conn),
    interrupt_before=["table_selection", "human_review"],
)
```

Each session gets a unique `thread_id`. FastAPI passes `{"configurable": {"thread_id": session_id}}` on every `graph.stream()` / `graph.get_state()` / `graph.update_state()` call.

---

## 8. FastAPI API Design

### Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/sessions` | Create session; return `{session_id}` |
| `POST` | `/sessions/{id}/upload` | Upload files (multipart); save to `data/uploads/{session_id}/` |
| `POST` | `/sessions/{id}/start` | Trigger graph execution; returns immediately; progress via SSE |
| `GET` | `/sessions/{id}/events` | SSE stream — pushes `{type, payload}` events as graph advances |
| `GET` | `/sessions/{id}/schema` | Return parsed `target_tables` + current `selected_tables` |
| `GET` | `/sessions/{id}/catalog` | Return `intermediate_catalog` (all profiled source columns) |
| `POST` | `/sessions/{id}/select-tables` | Set selected tables and trigger mapping inference |
| `GET` | `/sessions/{id}/mappings` | Return current `proposed_mappings` |
| `PUT` | `/sessions/{id}/mappings/{id}` | Edit a single mapping row |
| `POST` | `/sessions/{id}/confirm` | Run migration with confirmed mappings |
| `POST` | `/sessions/{id}/remigrate` | Re-run migration with updated mappings (from Done panel) |
| `GET` | `/sessions/{id}/validate` | Re-run FK/NULL validation on existing session DB |
| `GET` | `/sessions/{id}/result` | Return `{rows_loaded, stage, validation_results, error}` |
| `GET` | `/sessions/{id}/download/db` | Stream session `migration.db` as binary download |
| `GET` | `/sessions/{id}/download/report` | Stream `report.json` as download |
| `POST` | `/sessions/{id}/reset` | Delete session uploads and SSE queue |

### SSE Event Types

```jsonc
{"type": "profiling_progress",  "payload": {"file": "eu.csv",     "columns": 8}}
{"type": "ddl_parsed",          "payload": {"tables": [{"name": "customers", "columns": 5}, ...]}}
{"type": "mapping_ready",       "payload": {"mappings": [...]}}
{"type": "migration_progress",  "payload": {"table": "customers", "file": "eu.csv", "rows": 200}}
{"type": "migration_progress",  "payload": {"table": "addresses", "file": "eu.csv", "rows": 0, "skipped": true, "reason": "..."}}
{"type": "done",                "payload": {"total_rows": 900}}
{"type": "error",               "payload": {"message": "..."}}
{"type": "stream_end"}
```

---

## 9. Database Strategy

### Two Databases per Migration

| Database | Path | Lifecycle | Insert strategy |
|---|---|---|---|
| Session DB | `data/uploads/<id>/migration.db` | Recreated each run (DROP + CREATE) | `INSERT` |
| Project DB | `data/database/project.db` | Persistent across all sessions | `INSERT OR REPLACE` on PK |

The session DB gives a clean snapshot of this exact migration. The project DB accumulates data across all sessions — repeated primary keys are overwritten by the latest run.

### Inspecting Data

```powershell
# SQLite CLI
sqlite3 data/database/project.db ".tables"
sqlite3 data/database/project.db < data/database/scripts/select_all.sql

# Python runner (no sqlite3 CLI required)
.\\venv\\Scripts\\python.exe data/database/run_sql.py data/database/scripts/table_counts.sql
.\\venv\\Scripts\\python.exe data/database/run_sql.py data/database/scripts/table_counts.sql --db data/uploads/<id>/migration.db
```

### Included SQL Scripts

| Script | Purpose |
|---|---|
| `scripts/select_all.sql` | SELECT * from all tables |
| `scripts/table_counts.sql` | Row count per table |
| `scripts/delete_all_data.sql` | DELETE all rows (keeps schema) |
| `scripts/drop_all_tables.sql` | DROP all tables |

---

## 10. Environment Variables Reference

### Azure AI Foundry

| Variable | Required | Description |
|---|---|---|
| `LLM_PROVIDER` | Yes | Set to `azure` |
| `AZURE_OPENAI_ENDPOINT` | Yes | e.g. `https://<resource>.openai.azure.com/openai/v1/` |
| `AZURE_OPENAI_API_KEY` | Yes | Azure API key |
| `AZURE_OPENAI_DEPLOYMENT` | Yes | Deployment name for mapping inference (e.g. `gpt-4.1`) |
| `AZURE_OPENAI_DEPLOYMENT_FAST` | Yes | Deployment name for DDL parsing fallback |

### Ollama (local)

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | Set to `ollama` (or omit) |
| `LLM_BASE_URL` | `http://localhost:11434/v1` | Ollama endpoint |
| `LLM_MODEL` | — | Model for mapping inference (e.g. `qwen2.5:7b`) |
| `LLM_MODEL_FAST` | — | Model for DDL parsing fallback (e.g. `qwen3.5:4b`) |
| `LLM_API_KEY` | `ollama` | Ignored by Ollama but required by the SDK |

### Storage

| Variable | Default | Description |
|---|---|---|
| `UPLOAD_DIR` | `data/uploads` | Session upload root |
| `CHECKPOINT_DB` | `data/checkpoints.db` | LangGraph SqliteSaver |
| `MIGRATION_DB` | `data/database/project.db` | Shared project database |
| `PROFILER_SAMPLE_ROWS` | `1000` | Max rows profiled per file (0 = all) |

---

## 11. Technology Stack

| Layer | Technology | Notes |
|---|---|---|
| Agent orchestration | **LangGraph** ≥ 1.2 | `StateGraph`, `SqliteSaver`, `interrupt_before` |
| LLM inference | **Azure AI Foundry** (default) | OpenAI-compatible `/openai/v1/` endpoint; `gpt-4.1` deployments |
| LLM inference (alt) | **Ollama** + OpenAI Python SDK | Local; no API key required |
| LLM client factory | `llm_client.py` | Provider-agnostic; reads `LLM_PROVIDER` env var |
| HTTP server | **FastAPI** + `uvicorn` | SSE via `StreamingResponse` + async generator |
| Data profiling | **pandas** | CSV + JSON support |
| DDL parsing | **sqlglot** | Pure Python SQL parser; LLM fallback |
| Target database | **SQLite** (via Python `sqlite3`) | Session DB + project DB |
| Checkpoint database | **SQLite** (`SqliteSaver`) | Separate `checkpoints.db` |
| UI | Plain HTML + CSS + vanilla JS | Single file; no build step |
| Auth (Azure) | **azure-identity** | Installed; API key auth used currently |

---

## 12. Project Structure

```
DBMapper/
├── main.py                  # FastAPI app — all routes, SSE, session management
├── graph.py                 # LangGraph StateGraph (6 nodes, 2 interrupt points)
├── models.py                # MigrationState TypedDict + supporting TypedDicts
├── llm_client.py            # LLM client factory — Azure AI Foundry or Ollama
├── agents/
│   ├── file_profiler.py     # Pandas column profiling — no LLM
│   ├── ddl_parser.py        # sqlglot DDL parsing + LLM fallback
│   ├── table_selection.py   # Interrupt node — user picks tables
│   ├── mapping_inference.py # LLM mapping inference (selected tables only)
│   ├── human_review.py      # Interrupt node — user reviews mappings
│   └── data_migration.py    # SQLite ETL — session DB + project DB
├── static/
│   └── index.html           # Single-page UI (vanilla JS)
├── data/
│   ├── raw/                 # Sample test files
│   ├── uploads/             # Per-session: source files + migration.db + report.json
│   └── database/
│       ├── project.db       # Shared persistent SQLite database
│       ├── run_sql.py       # Python CLI runner for SQL scripts
│       └── scripts/         # Utility SQL: select_all, table_counts, delete, drop
├── requirements.txt
├── pyproject.toml
└── .env
```

---

## 13. Functional Requirements

| ID | Requirement |
|---|---|
| FR-01 | The system MUST accept 1 to N `.csv` or `.json` files per session |
| FR-02 | Exactly one `.sql` DDL file MUST be uploaded per session |
| FR-03 | All source files MUST be profiled before the LLM mapping call is made |
| FR-04 | The user MUST be able to select a subset of DDL tables before mapping inference runs |
| FR-05 | Mapping inference MUST run only against the selected tables |
| FR-06 | The DDL parser MUST fall back to an LLM call if `sqlglot` fails |
| FR-07 | The MappingInferenceAgent MUST produce exactly one `MappingItem` per column per selected table |
| FR-08 | The graph MUST pause at `table_selection` and `human_review` before executing those nodes |
| FR-09 | The user MUST be able to change source file, source column, and transformation for any mapping |
| FR-10 | "Confirm & Migrate" MUST be disabled until all NOT NULL / PK columns have a source assigned |
| FR-11 | Migration MUST write to both the session DB (full recreate) and the project DB (INSERT OR REPLACE) |
| FR-12 | The session `migration.db` and `report.json` MUST be downloadable from the UI |
| FR-13 | Switching between Azure AI Foundry and Ollama MUST require only `.env` changes |
| FR-14 | The SSE stream MUST emit at least one event per file during profiling and one per table/file during migration |

---

## 14. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-01 | Profiling a 10 MB CSV MUST complete in < 10 s on a standard laptop |
| NFR-02 | End-to-end latency (upload to mapping table displayed) MUST be < 60 s for a 3-file, 50 MB input |
| NFR-03 | The server MUST support at least 5 concurrent sessions without state leakage |
| NFR-04 | A server restart MUST allow an in-progress session to resume from the last completed graph node |
| NFR-05 | All uploaded files MUST be stored only within the session directory; path traversal MUST return HTTP 400 |
| NFR-06 | The UI MUST work in current Chrome, Firefox, and Safari without build tools |

---

## 15. Open Questions

1. **Entra ID auth:** Currently using API key auth for Azure AI Foundry. Should we add `DefaultAzureCredential` support via `azure-identity` for production/managed-identity scenarios?

2. **Transformation expressions:** `df.eval()` handles simple column renames and basic arithmetic. Should a future version support richer expressions (e.g. Jinja2 templates, Python lambdas)?

3. **Duplicate rows across files:** If two source files share the same primary-key values, the current behaviour is to insert both (session DB) or replace (project DB). Should there be a de-duplication pass?

4. **Large file strategy:** pandas loads entire files into memory. At what file size should chunked insertion be enabled?

5. **Session TTL:** How long should uploaded files and checkpoints be retained? Should a cleanup job run on server startup?

---

*Updated 2026-06-25 — v1.2*
