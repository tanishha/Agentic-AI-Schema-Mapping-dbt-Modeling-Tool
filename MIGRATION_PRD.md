# PRD: Agentic Schema Migration Tool

**Version:** 1.1  
**Date:** 2026-06-24  
**Status:** Draft

---

## 1. Executive Summary

A browser-based tool that accepts arbitrary CSV and JSON source files plus a target SQL DDL file, uses a LangGraph multi-agent pipeline to profile the sources, infer column mappings with confidence scores, present them in an editable review table, and — once the user confirms — migrates all data into a local SQLite database matching the target schema.

The system is built on three components: a **LangGraph** agent graph for all AI-driven stages, a **FastAPI** server for the HTTP + Server-Sent Events layer, and a **plain HTML/JS** single-page UI. All inference runs locally via **Ollama** — no external API keys or internet access required. The primary model is `qwen2.5:7b` (mapping inference) with `qwen3.5:4b` available as a lighter alternative for faster DDL parsing.

---

## 2. Goals

| ID | Goal |
|---|---|
| G1 | Accept any number of CSV and JSON files alongside a SQL DDL file in a single upload step |
| G2 | Profile all source files and build a unified intermediate column catalog before any LLM call |
| G3 | Infer column mappings (source → target) with a confidence score and human-readable reason for each |
| G4 | Present mappings in an editable table — the human is always in control before any data moves |
| G5 | Execute the confirmed mappings and load data into a local SQLite table that matches the DDL exactly |
| G6 | Stream progress to the UI via SSE so the user can see which stage the pipeline is in without polling |

### Out of scope (v1)

- Authentication / multi-tenancy
- Cloud database targets (Postgres, MySQL, etc.)
- Complex SQL expressions or multi-column derived fields in mappings
- Scheduling or recurring batch loads
- Mapping to more than one target table per session

---

## 3. High-Level Architecture

```
Browser (HTML/JS)
      │  multipart upload (files + DDL)
      │  SSE stream (progress events)
      │  REST (mappings, confirm)
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
  [MappingInferenceAgent]  ◄── LLM (OpenAI-compatible endpoint)     │
       │ returns mappings + confidence                              │
       ▼                                                            │
  [HumanReviewNode]  ◄─── interrupt_before ──────── user edits / confirm
       │                                                            │
       ▼                                                            │
  [DataMigrationAgent]                                              │
       │ creates SQLite table from DDL                              │
       │ transforms + inserts rows                                  │
       ▼                                                            │
     DONE                                                           │
─────────────────────────────────────────────────────────────────────
```

**LangGraph** owns all graph state and checkpointing.  
**FastAPI** is a thin adapter: it receives HTTP requests, forwards them into the graph, and pushes graph events back to the browser over SSE.  
**SQLite** serves two roles: graph checkpoints (via `SqliteSaver`, table `checkpoints`) and the migration target (a separate file or separate tables named by the DDL).

---

## 4. User Flow

### Step 1 — Upload

```
┌─────────────────────────────────────────────────────┐
│  Drop or browse files                               │
│  ┌────────────────────────────────────────────────┐ │
│  │  📄 customers_eu.csv       × remove            │ │
│  │  📄 customers_us.json      × remove            │ │
│  │  📄 orders_legacy.csv      × remove            │ │
│  │  📋 target_schema.sql      × remove  [DDL]     │ │
│  └────────────────────────────────────────────────┘ │
│  [+ Add more files]              [Start Profiling →] │
└─────────────────────────────────────────────────────┘
```

- User drops any number of `.csv` or `.json` data files and exactly one `.sql` DDL file.
- Clicking **Start Profiling** triggers `POST /sessions/{id}/start`.
- The button label changes to "Profiling…" with a spinner; the upload panel becomes read-only.

---

### Step 2 — Profiling & DDL Parse (automated)

```
┌─────────────────────────────────────────────────────┐
│  Pipeline Progress                                  │
│  ✅ customers_eu.csv      profiled (8 columns)      │
│  ✅ customers_us.json     profiled (11 columns)     │
│  ✅ orders_legacy.csv     profiled (6 columns)      │
│  ✅ target_schema.sql     parsed → 9 target cols    │
│  ────────────────────────────────────────────────── │
│  🔄 Inferring mappings…                             │
└─────────────────────────────────────────────────────┘
```

Progress events are pushed via SSE as each file is processed. No user action required.

---

### Step 3 — Mapping Review (human in the loop)

```
┌──────────────────────────────────────────────────────────────────┐
│  Proposed Mappings — review and edit before migrating            │
├──────────────────────┬───────────────────┬────────┬─────────────┤
│  Target Column       │  Source           │  Conf  │  Reason     │
├──────────────────────┼───────────────────┼────────┼─────────────┤
│  customer_id         │  cust_id          │  98%   │  exact name │
│  full_name           │  name_first +     │  91%   │  concat     │
│                      │  name_last        │        │  detected   │
│  email               │  email_address    │  96%   │  near-match │
│  phone               │  [▼ select col]   │  --    │  not found  │
│  date_of_birth       │  dob              │  88%   │  abbrev.    │
│  address             │  street_address   │  85%   │  semantic   │
│  city                │  [▼ select col]   │  --    │  not found  │
│  country             │  country_code     │  72%   │  low conf   │
│  signup_date         │  created_at       │  94%   │  semantic   │
├──────────────────────┴───────────────────┴────────┴─────────────┤
│  [← Back]                              [Confirm & Migrate →]    │
└──────────────────────────────────────────────────────────────────┘
```

- Each row in the table is editable: the **Source** cell is a dropdown of all columns from the intermediate catalog plus `— skip —`.
- Confidence badge is colour-coded: green ≥ 90 %, amber 70–89 %, red < 70 %.
- The **Confirm & Migrate** button sends `POST /sessions/{id}/confirm` with the (possibly edited) mappings.
- Clicking **Back** returns to the upload step and resets the session.

---

### Step 4 — Migration

```
┌─────────────────────────────────────────────────────┐
│  Migration Progress                                 │
│  ✅ Created table "customers" in migration.db       │
│  ✅ customers_eu.csv       →  1,204 rows loaded     │
│  ✅ customers_us.json      →  3,871 rows loaded     │
│  ✅ orders_legacy.csv      →  skipped (no match)    │
│  ────────────────────────────────────────────────── │
│  Done — 5,075 rows in customers                     │
│  [Download migration.db]   [Start New Migration]    │
└─────────────────────────────────────────────────────┘
```

---

## 5. Agent Decomposition

### 5.1 Shared state

```python
from typing import TypedDict, Optional, List, Literal

class ColumnProfile(TypedDict):
    source_file: str          # original filename
    column_name: str
    inferred_type: str        # "string" | "integer" | "float" | "date" | "boolean"
    null_pct: float
    distinct_count: int
    sample_values: List[str]

class TargetColumn(TypedDict):
    name: str
    sql_type: str
    nullable: bool
    primary_key: bool

class MappingItem(TypedDict):
    id: str                   # stable uuid for the row
    target_column: str
    source_file: Optional[str]
    source_column: Optional[str]
    transformation: Optional[str]  # e.g. "concat(name_first, ' ', name_last)"
    confidence: float         # 0.0–1.0
    reason: str

class MigrationState(TypedDict):
    session_id: str
    stage: Literal[
        "UPLOADING", "PROFILING", "READY", "MAPPING",
        "REVIEWING", "MIGRATING", "DONE", "ERROR"
    ]
    # inputs
    source_files: List[str]          # file paths on disk
    ddl_content: str
    # intermediate
    intermediate_catalog: List[ColumnProfile]
    target_schema_name: str          # table name from DDL
    target_columns: List[TargetColumn]
    # mappings
    proposed_mappings: List[MappingItem]
    confirmed_mappings: List[MappingItem]
    # outputs
    rows_loaded: dict                # {filename: row_count}
    error: Optional[str]
    # SSE event buffer (drained by FastAPI)
    events: List[dict]
```

---

### 5.2 FileProfilerAgent

**Responsibility:** Read every source file, compute per-column statistics, build the unified intermediate catalog.

**Inputs:** `source_files`, `session_id`  
**Outputs:** `intermediate_catalog`, `stage = "READY"`

**Logic:**
- For each file, detect format from extension (`.csv` → pandas `read_csv`, `.json` → pandas `read_json` with orient auto-detection).
- Compute per column: `inferred_type`, `null_pct`, `distinct_count`, `sample_values` (up to 5 non-null values).
- Append a progress event to `state["events"]` after each file.
- No LLM call; pure pandas computation.

**Tools:** `profile_file(path) → List[ColumnProfile]`

---

### 5.3 DDLParserAgent

**Responsibility:** Parse the SQL DDL string into a structured target schema.

**Inputs:** `ddl_content`  
**Outputs:** `target_schema_name`, `target_columns`, `stage = "MAPPING"`

**Logic:**
- Parse DDL with `sqlglot` (no LLM required for well-formed DDL).
- Extract table name, column names, SQL types, `NOT NULL` constraints, `PRIMARY KEY` markers.
- If `sqlglot` fails to parse, fall back to an Ollama LLM call (`qwen3.5:4b` — faster for this simple extraction) to extract the schema as structured JSON.
- Emit a progress event.

**Tools:** `parse_ddl_sqlglot(ddl) → TargetSchema`, `parse_ddl_llm(ddl) → TargetSchema`

---

### 5.4 MappingInferenceAgent

**Responsibility:** Call the LLM to propose a mapping from the intermediate catalog to the target schema.

**Inputs:** `intermediate_catalog`, `target_columns`, `target_schema_name`  
**Outputs:** `proposed_mappings`, `stage = "REVIEWING"`

**Logic:**
- Build a prompt with:
  - Target schema (column name, SQL type, nullable, PK)
  - Intermediate catalog (all source columns across all files, with sample values)
- Parse the LLM response as a JSON array of `MappingItem`.
- Retry once on `JSONDecodeError`.
- Assign a stable UUID to each mapping row.
- Emit a progress event.

**Prompt skeleton:**
```
You are a data migration assistant.

Target schema — table "{target_schema_name}":
{target_columns_json}

Available source columns (all files combined):
{intermediate_catalog_json}

For each target column, return the best-matching source column, a confidence score (0.0–1.0), an optional transformation expression, and a reason. Return a JSON array only — no prose.

If no source column maps to a target column, set source_column to null and confidence to 0.0.
```

**Model:** `qwen2.5:7b` (default) via Ollama's OpenAI-compatible endpoint at `http://localhost:11434/v1`. `qwen3.5:4b` can be substituted via `LLM_MODEL` env var for faster responses at slightly lower accuracy. No API key required — Ollama accepts any non-empty string.

---

### 5.5 HumanReviewNode *(interrupt point)*

**Responsibility:** Pause the graph, surface `proposed_mappings` to the UI, and wait for the user to confirm or edit.

**Mechanism:** LangGraph `interrupt_before=["human_review"]`. Graph execution halts; FastAPI returns the current `proposed_mappings` in the SSE stream. When `POST /sessions/{id}/confirm` arrives with the final mapping list, FastAPI calls `graph.invoke({"confirmed_mappings": payload}, config)` to resume.

**No LLM call in this node.** It only copies `confirmed_mappings` into state and sets `stage = "MIGRATING"`.

---

### 5.6 DataMigrationAgent

**Responsibility:** Create the SQLite target table (if it doesn't exist), transform source columns according to confirmed mappings, and insert rows.

**Inputs:** `confirmed_mappings`, `source_files`, `target_schema_name`, `ddl_content`  
**Outputs:** `rows_loaded`, `stage = "DONE"`

**Logic:**
1. Open (or create) `data/migration.db`.
2. Execute the DDL string verbatim via `conn.executescript(ddl_content)` to create the target table.
3. For each source file:
   a. Load into a pandas DataFrame.
   b. For each confirmed mapping, rename or compute the source column into the target column name (apply `transformation` expression via `df.eval()` for simple concat/cast, or a fallback lambda for anything more complex).
   c. Select only target columns; drop rows where primary-key column is null.
   d. `df.to_sql(target_schema_name, conn, if_exists="append", index=False)`.
   e. Record row count in `rows_loaded[filename]`.
4. Emit a progress event per file.

**Tools:** `create_target_table(conn, ddl)`, `transform_and_load(conn, df, mappings, table_name) → int`

---

## 6. Graph Wiring

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

graph = StateGraph(MigrationState)

graph.add_node("file_profiler",      file_profiler_agent)
graph.add_node("ddl_parser",         ddl_parser_agent)
graph.add_node("mapping_inference",  mapping_inference_agent)
graph.add_node("human_review",       human_review_node)
graph.add_node("data_migration",     data_migration_agent)

graph.set_entry_point("file_profiler")
graph.add_edge("file_profiler",     "ddl_parser")
graph.add_edge("ddl_parser",        "mapping_inference")
graph.add_edge("mapping_inference", "human_review")
graph.add_edge("human_review",      "data_migration")
graph.add_edge("data_migration",    END)

checkpointer = SqliteSaver.from_conn_string("data/checkpoints.db")

compiled = graph.compile(
    checkpointer=checkpointer,
    interrupt_before=["human_review"],
)
```

Each session gets a unique `thread_id`. FastAPI passes `{"configurable": {"thread_id": session_id}}` on every `graph.invoke()` / `graph.stream()` call.

---

## 7. FastAPI API Design

### Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/sessions` | Create session; return `{session_id}` |
| `POST` | `/sessions/{id}/upload` | Upload files (multipart); save to `data/uploads/{session_id}/`; return file list |
| `POST` | `/sessions/{id}/start` | Trigger graph execution; return immediately; progress via SSE |
| `GET` | `/sessions/{id}/events` | SSE stream — pushes `{type, payload}` events as graph advances |
| `GET` | `/sessions/{id}/mappings` | Return current `proposed_mappings` (after REVIEWING stage) |
| `PUT` | `/sessions/{id}/mappings/{mapping_id}` | Edit a single mapping row (source_file, source_column, transformation) |
| `POST` | `/sessions/{id}/confirm` | Resume graph with confirmed mappings |
| `GET` | `/sessions/{id}/result` | Return `{rows_loaded, stage, error}` |
| `GET` | `/sessions/{id}/download` | Stream `migration.db` as binary download |
| `POST` | `/sessions/{id}/reset` | Delete session state and uploaded files |

### SSE event types

```jsonc
{"type": "profiling_progress",  "payload": {"file": "eu.csv",     "columns": 8}}
{"type": "profiling_progress",  "payload": {"file": "us.json",    "columns": 11}}
{"type": "ddl_parsed",          "payload": {"table": "customers", "columns": 9}}
{"type": "mapping_ready",       "payload": {"mappings": [...]}}
{"type": "migration_progress",  "payload": {"file": "eu.csv",     "rows": 1204}}
{"type": "done",                "payload": {"total_rows": 5075}}
{"type": "error",               "payload": {"message": "..."}}
```

### File storage

Uploaded files are stored at `data/uploads/{session_id}/{filename}` and deleted when the session is reset or the server restarts. No cloud storage required.

---

## 8. UI Design

Single HTML file (`static/index.html`), vanilla JS, no framework.

### Layout — four sequential panels; only the active panel is visible

```
┌─────────────────────────────────────────────────────────┐
│  [1] Upload  [2] Profiling  [3] Review  [4] Done        │  ← step indicator
└─────────────────────────────────────────────────────────┘
```

#### Panel 1 — Upload
- File drop-zone accepting `.csv`, `.json`, `.sql`
- Uploaded files listed with type badge (DATA / DDL) and remove button
- Exactly one DDL file must be present before "Start" is enabled
- "Start Profiling" button: `POST /sessions`, then `POST /sessions/{id}/upload`, then `POST /sessions/{id}/start`, then open SSE stream

#### Panel 2 — Profiling progress
- One status line per file (spinner → ✅ with column count)
- "Inferring mappings…" line at the bottom during MappingInferenceAgent
- Auto-advances to Panel 3 when `mapping_ready` SSE event arrives

#### Panel 3 — Mapping review table

```
Target Column | Source File | Source Column | Confidence | Reason | Action
─────────────────────────────────────────────────────────────────────────
customer_id   | eu.csv      | [▼ cust_id ]  |  ████ 98%  | exact  |  ✏
full_name     | eu.csv      | [▼ name_f.. ] |  ███░ 91%  | concat |  ✏
phone         | —           | [▼ — skip — ] |  ░░░░  0%  | none   |  ✏
```

- **Source File** dropdown: lists all uploaded files + "— skip —"
- **Source Column** dropdown: filters to columns from the selected source file
- Confidence bar uses CSS width; green/amber/red based on thresholds
- Inline editing saves to `PUT /sessions/{id}/mappings/{id}` on blur
- "Confirm & Migrate" button enabled only when all non-nullable, non-PK target columns have a source assigned
- "← Back" resets to Panel 1

#### Panel 4 — Migration result
- One status line per file (rows loaded)
- Total row count
- "Download migration.db" button (`GET /sessions/{id}/download`)
- "Start New Migration" resets to Panel 1

### Security
- All user-supplied text inserted into the DOM via `textContent`, never `innerHTML`
- File paths server-side resolved within the session upload directory (no path traversal)
- No `eval()` on user-supplied strings

---

## 9. Functional Requirements

| ID | Requirement |
|---|---|
| FR-01 | The system MUST accept 1 to N `.csv` or `.json` files per session |
| FR-02 | Exactly one `.sql` DDL file MUST be uploaded per session; the UI MUST reject sessions with zero or more than one DDL file |
| FR-03 | All source files MUST be profiled before the LLM mapping call is made |
| FR-04 | The intermediate catalog MUST contain one entry per (file, column) pair; duplicate column names from different files MUST be distinguished by their source file |
| FR-05 | The DDL parser MUST extract table name, column names, SQL types, nullability, and primary key constraints |
| FR-06 | If DDL parsing via `sqlglot` fails, the system MUST retry using the LLM fallback before returning an error |
| FR-07 | The MappingInferenceAgent MUST produce exactly one `MappingItem` per target column |
| FR-08 | The graph MUST pause at `HumanReviewNode` until the client calls `POST /sessions/{id}/confirm` |
| FR-09 | The user MUST be able to change the source file, source column, and transformation for any mapping row before confirming |
| FR-10 | "Confirm & Migrate" MUST be disabled until all `NOT NULL` target columns without a `DEFAULT` have an assigned source column |
| FR-11 | The DataMigrationAgent MUST execute the DDL verbatim to create the target table before inserting any rows |
| FR-12 | Insertion MUST be idempotent per session: if migration is re-run, existing rows for this session MUST be deleted before re-inserting (use a `_source_session` shadow column or a delete-first strategy) |
| FR-13 | The SSE stream MUST emit at minimum one event per file during profiling and one per file during migration |
| FR-14 | The `migration.db` file MUST be downloadable via `GET /sessions/{id}/download` after migration completes |

---

## 10. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-01 | Profiling a 10 MB CSV MUST complete in < 10 s on a standard laptop |
| NFR-02 | End-to-end latency (upload to mapping table displayed) for a 3-file, 50 MB total input MUST be < 60 s |
| NFR-03 | The server MUST support at least 5 concurrent sessions without state leakage between sessions |
| NFR-04 | A server restart MUST allow an in-progress session to resume from the last completed graph node (via checkpointer) |
| NFR-05 | All uploaded files MUST be stored only within the session directory; path traversal MUST return HTTP 400 |
| NFR-06 | The UI MUST work in current Chrome, Firefox, and Safari without build tools |

---

## 11. Technology Stack

| Layer | Technology | Notes |
|---|---|---|
| Agent orchestration | **LangGraph** `>=0.2` | `StateGraph`, `SqliteSaver`, `interrupt_before` |
| LLM inference | **Ollama** (local) + OpenAI Python SDK | Ollama exposes `http://localhost:11434/v1`; SDK uses it via `base_url`. No cloud calls, no API key. |
| HTTP server | **FastAPI** + `uvicorn` | SSE via `fastapi.responses.StreamingResponse` + async generator |
| Data profiling | **pandas** | No Spark dependency; all source files profiled with pandas |
| DDL parsing | **sqlglot** | Pure Python SQL parser; no DB connection needed |
| Target database | **SQLite** (via Python `sqlite3`) | One `migration.db` per session |
| Checkpoint database | **SQLite** (`SqliteSaver`) | Separate `checkpoints.db`; no schema conflict |
| UI | Plain HTML + CSS + vanilla JS | Single file; no build step; no frameworks |
| File uploads | FastAPI `UploadFile` | Saved to `data/uploads/{session_id}/` |

**New dependencies (approximate):**

```
fastapi>=0.115
uvicorn>=0.30
langgraph>=0.2
openai>=1.0
pandas>=2.0
sqlglot>=25.0
python-dotenv>=1.0
```

---

## 12. Environment Variables

```ini
# Ollama runs locally — no external service needed
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:7b          # primary model — better mapping quality
LLM_MODEL_FAST=qwen3.5:4b     # used for DDL parse fallback (faster, lighter)
LLM_API_KEY=ollama             # Ollama ignores this but OpenAI SDK requires a non-empty value

UPLOAD_DIR=data/uploads
CHECKPOINT_DB=data/checkpoints.db
MIGRATION_DB=data/migration.db
PROFILER_SAMPLE_ROWS=10000     # max rows to profile per file (0 = all)
```

---

## 13. Directory Layout

```
/
├── main.py                 # FastAPI app, session management, SSE handler
├── graph.py                # LangGraph graph definition and compilation
├── agents/
│   ├── file_profiler.py    # FileProfilerAgent
│   ├── ddl_parser.py       # DDLParserAgent
│   ├── mapping_inference.py# MappingInferenceAgent
│   ├── human_review.py     # HumanReviewNode
│   └── data_migration.py   # DataMigrationAgent
├── models.py               # MigrationState TypedDict + all TypedDicts
├── static/
│   └── index.html          # Single-page UI
├── data/
│   ├── uploads/            # Per-session uploaded files
│   ├── checkpoints.db      # LangGraph SqliteSaver
│   └── migration.db        # Target SQLite database
├── requirements.txt
└── .env
```

---

## 14. Migration / Bootstrap Path

This is a greenfield build. No migration from an existing system is required.

**Milestone 1 — Backend skeleton**
- FastAPI routes: `/sessions`, `/upload`, `/start`, `/events` (SSE stub)
- LangGraph graph wired with no-op nodes
- SqliteSaver connected

**Milestone 2 — Profiling + DDL parsing**
- `FileProfilerAgent` with pandas
- `DDLParserAgent` with sqlglot + LLM fallback
- SSE events emitted per file; UI Panel 1 + 2 functional

**Milestone 3 — Mapping inference + review**
- `MappingInferenceAgent` LLM call + JSON parse + retry
- `HumanReviewNode` interrupt wired
- UI Panel 3: editable mapping table, `PUT /mappings/{id}`, confirm button

**Milestone 4 — Migration + download**
- `DataMigrationAgent`: DDL execution, transform + insert, progress SSE
- UI Panel 4: row counts, download link
- End-to-end test with three heterogeneous source files

---

## 15. Open Questions

1. **Multi-table DDL:** If the DDL defines more than one table, should the system ask the user which table to map to, or map to all tables where a column match is found?

2. **Transformation expressions:** `df.eval()` handles simple concat and cast. Should v1 support a more expressive transform syntax (e.g., jinja2 templates per row), or restrict to single-column renames plus concat?

3. **Duplicate rows across files:** If two source files share the same primary-key values, should the migration agent skip duplicates, overwrite, or raise an error?

4. **Large file strategy:** pandas loads the whole file into memory. At what file size should the system switch to chunked insertion (`chunksize` in `to_sql`) or offer a Spark back-end?

5. **Session TTL:** How long should uploaded files and checkpoints be retained on disk? Should a background cleanup task run on server startup?

---

*Prepared 2026-06-24*
