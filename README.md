# DBMapper

An agentic schema-migration tool that maps CSV/JSON source data to a target SQLite database using an LLM. Upload your source files and a SQL DDL, let the pipeline profile columns and infer mappings, review and edit the proposed mappings in a browser UI, then confirm to run the migration — all without writing a single transform script.

Supports **Azure AI Foundry** (default) and **Ollama** (local) as interchangeable LLM backends, switchable via a single environment variable.

---

## How it works

```
Upload files → Profile columns → Parse DDL → Select tables
    → LLM mapping inference → Human review & edit
    → Confirm → Migrate to SQLite → Validate + ERD
```

The pipeline is built with **LangGraph** and pauses at two points:
1. **Table selection** — pick which tables in the DDL to map and migrate
2. **Mapping review** — inspect and correct every proposed source→target column mapping

After confirmation, data is loaded into a session-specific SQLite database **and** mirrored into the shared project database. A validation report and ERD diagram are shown on completion.

---

## Requirements

| Dependency | Version |
|---|---|
| Python | ≥ 3.12 |

### LLM Backend (choose one)

**Azure AI Foundry (default)**

| Requirement | Notes |
|---|---|
| Azure AI Foundry resource | An OpenAI-compatible endpoint (e.g. `gpt-4.1`) |
| `AZURE_OPENAI_ENDPOINT` | `https://<resource>.openai.azure.com/openai/v1/` |
| `AZURE_OPENAI_API_KEY` | Your Azure API key |
| `AZURE_OPENAI_DEPLOYMENT` | Deployment name for mapping inference |
| `AZURE_OPENAI_DEPLOYMENT_FAST` | Deployment name for DDL parsing fallback |

**Ollama (local alternative)**

| Requirement | Notes |
|---|---|
| [Ollama](https://ollama.com) | Running locally on port 11434 |
| `qwen2.5:7b` | `ollama pull qwen2.5:7b` — mapping inference |
| `qwen3.5:4b` | `ollama pull qwen3.5:4b` — DDL parsing fallback |

---

## Setup

### 1. Clone and create the environment

```bash
git clone https://github.com/whatiskeptiname/DBMapper.git
cd DBMapper

# Using uv (recommended)
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Or plain pip
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment variables

Create a `.env` file in the project root. Choose one of the two LLM provider blocks:

**Option A — Azure AI Foundry**

```ini
LLM_PROVIDER=azure

AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/openai/v1/
AZURE_OPENAI_API_KEY=<your-api-key>
AZURE_OPENAI_DEPLOYMENT=gpt-4.1
AZURE_OPENAI_DEPLOYMENT_FAST=gpt-4.1

UPLOAD_DIR=data/uploads
CHECKPOINT_DB=data/checkpoints.db
MIGRATION_DB=data/database/project.db
PROFILER_SAMPLE_ROWS=1000
```

**Option B — Ollama (local)**

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

#### Full environment variable reference

| Variable | Provider | Description |
|---|---|---|
| `LLM_PROVIDER` | both | `azure` or `ollama` (default: `ollama`) |
| `AZURE_OPENAI_ENDPOINT` | azure | Full Azure OpenAI endpoint URL |
| `AZURE_OPENAI_API_KEY` | azure | Azure API key |
| `AZURE_OPENAI_DEPLOYMENT` | azure | Deployment name for mapping inference |
| `AZURE_OPENAI_DEPLOYMENT_FAST` | azure | Deployment name for DDL parsing fallback |
| `LLM_BASE_URL` | ollama | OpenAI-compatible base URL |
| `LLM_MODEL` | ollama | Model for mapping inference |
| `LLM_MODEL_FAST` | ollama | Model for DDL parsing fallback |
| `LLM_API_KEY` | ollama | API key (`ollama` for local Ollama) |
| `UPLOAD_DIR` | both | Where uploaded session files are stored |
| `CHECKPOINT_DB` | both | LangGraph checkpoint database path |
| `MIGRATION_DB` | both | Shared project database path |
| `PROFILER_SAMPLE_ROWS` | both | Max rows sampled per file during profiling |

### 3. Start the server

```bash
uvicorn main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

---

## Usage

1. **Upload** — drag-and-drop one or more `.csv` / `.json` source files and exactly one `.sql` DDL file defining the target schema.
2. **Profiling** — the pipeline profiles source columns and parses the DDL (supports multi-table schemas with FK relationships).
3. **Select tables** — choose which tables from the DDL you want to map and migrate in this session.
4. **Review mappings** — each selected table is shown with proposed source→target column mappings, confidence scores, and a live schema diagram (ERD). Edit any mapping using the dropdowns.
5. **Confirm & Migrate** — click to run the migration. Progress is streamed in real time via SSE.
6. **Done** — view row counts, FK/NOT NULL validation results, the full ERD, and download the session SQLite database or a JSON report.
7. **Edit Mappings** — go back to the review panel and re-run migration without re-uploading.

---

## Database credentials

This project uses SQLite, so there is no database username, password, host, or port. The database is a normal file on disk:

- `data/uploads/<session-id>/migration.db` is the session-specific database.
- `data/database/project.db` is the shared project database.
- `data/checkpoints.db` is LangGraph's internal workflow checkpoint database.

When sharing the repo, do not commit `.env` because it can contain LLM API keys. Usually you share `.env.example`, source code, sample files, and setup steps. Decide separately whether to include generated `.db` files and uploaded data, depending on whether those files contain private data.

---

## Project structure

```
DBMapper/
├── main.py                  # FastAPI app — routes, SSE streaming, session management
├── graph.py                 # LangGraph StateGraph definition
├── models.py                # TypedDict state definitions
├── llm_client.py            # Shared LLM client factory (Azure AI Foundry / Ollama)
├── agents/
│   ├── file_profiler.py     # Pandas-based column profiling
│   ├── ddl_parser.py        # sqlglot DDL → TableSchema (LLM fallback)
│   ├── table_selection.py   # Interrupt node — user picks which tables to migrate
│   ├── mapping_inference.py # LLM mapping inference
│   ├── human_review.py      # Interrupt node — user reviews proposed mappings
│   └── data_migration.py    # SQLite migration + validation (session DB + project DB)
├── static/
│   └── index.html           # Single-page UI (vanilla JS, no build step)
├── data/
│   ├── raw/                 # Sample source files and target DDL for testing
│   ├── uploads/             # Per-session uploaded files + session migration.db
│   └── database/            # Shared project database
│       ├── project.db       # Persistent SQLite DB — accumulates across sessions
│       ├── run_sql.py       # Python CLI runner for SQL scripts (no sqlite3 CLI needed)
│       └── scripts/         # Utility SQL scripts (select_all, table_counts, etc.)
├── requirements.txt         # Pinned dependencies
├── pyproject.toml           # uv / PEP 517 project metadata
└── .env                     # Local config (not committed)
```

---

## Sample data

`data/raw/` contains source files and a multi-table DDL for end-to-end testing:

| File | Rows | Maps to |
|---|---|---|
| `crm_export.csv` | 200 | customers + contact_details + addresses |
| `legacy_users.json` | 150 | customers + contact_details + addresses |
| `subscriber_feed.csv` | 100 | contact_details + addresses |
| `target_schema.sql` | — | 3-table schema with FK relationships |

---

## Output

Each migration session stores files under `data/uploads/<session-id>/`:

```
data/uploads/<session-id>/
├── <source files>
├── target_schema.sql
├── migration.db    # Session-specific SQLite database (recreated each run)
└── report.json     # Full session metadata (mappings, row counts, validation)
```

In addition, migrated rows are written to the **shared project database**:

```
data/database/project.db
```

Session databases are fully recreated on each run. `project.db` keeps existing rows and uses `INSERT OR REPLACE` on primary-key conflicts — new keys accumulate and repeated keys are replaced by the latest migration.

Both files are downloadable from the Done panel in the UI.

### Inspecting the database

If you have the SQLite CLI installed:

```powershell
sqlite3 data/database/project.db ".tables"
sqlite3 data/database/project.db ".schema"
sqlite3 data/database/project.db < data/database/scripts/table_counts.sql
sqlite3 data/database/project.db < data/database/scripts/select_all.sql
sqlite3 data/database/project.db < data/database/scripts/delete_all_data.sql
```

If `sqlite3` is not installed, use the included Python runner:

```powershell
# Run against the project DB (default)
.\\venv\\Scripts\\python.exe data/database/run_sql.py data/database/scripts/table_counts.sql

# Run against a specific session DB
.\\venv\\Scripts\\python.exe data/database/run_sql.py data/database/scripts/table_counts.sql --db data/uploads/<session-id>/migration.db
```
