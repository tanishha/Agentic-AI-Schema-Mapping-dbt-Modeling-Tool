# DBMapper

An agentic schema-migration tool that maps CSV/JSON source data to a target SQLite database using a local LLM. Upload your source files and a SQL DDL, let the pipeline profile columns and infer mappings, review and edit the proposed mappings in a browser UI, then confirm to run the migration — all without writing a single transform script.

---

## How it works

```
Upload files → Profile columns → Parse DDL → LLM mapping inference
    → Human review & edit → Confirm → Migrate to SQLite → Validate + ERD
```

The pipeline is built with **LangGraph** and pauses before the migration step so you can review and correct every mapping. After confirmation, data is loaded into a per-session SQLite database and a validation report + ERD diagram are shown.

---

## Requirements

| Dependency | Version |
|---|---|
| Python | ≥ 3.12 |
| [Ollama](https://ollama.com) | running locally on port 11434 |
| qwen2.5:7b | `ollama pull qwen2.5:7b` |
| qwen3.5:4b | `ollama pull qwen3.5:4b` (DDL fallback) |

> You can swap models via the `.env` file — any OpenAI-compatible endpoint works.

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

Copy the example and fill in your values:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible LLM endpoint |
| `LLM_MODEL` | `qwen2.5:7b` | Model used for mapping inference |
| `LLM_MODEL_FAST` | `qwen3.5:4b` | Model used for DDL parsing fallback |
| `LLM_API_KEY` | `ollama` | API key (`ollama` for local Ollama) |
| `UPLOAD_DIR` | `data/uploads` | Where uploaded files are stored |
| `CHECKPOINT_DB` | `data/checkpoints.db` | LangGraph checkpoint database |
| `MIGRATION_DB` | `data/output` | Root dir for per-session migration databases |
| `PROFILER_SAMPLE_ROWS` | `1000` | Max rows sampled per file during profiling |

### 3. Start the server

```bash
uvicorn main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

---

## Usage

1. **Upload** — drag-and-drop one or more `.csv` / `.json` source files and exactly one `.sql` DDL file defining the target schema.
2. **Profiling** — the pipeline profiles source columns and parses the DDL (supports multi-table schemas with FK relationships).
3. **Review mappings** — each target table is shown with proposed source→target column mappings, confidence scores, and a live schema diagram (ERD). Edit any mapping using the dropdowns.
4. **Confirm & Migrate** — click to run the migration. Progress is streamed in real time.
5. **Done** — view row counts, FK/NOT NULL validation results, the full ERD, and download the SQLite database or a JSON report.
6. **Edit Mappings** — go back to the review panel and re-run migration without uploading again.

---

## Project structure

```
DBMapper/
├── main.py                  # FastAPI app — routes, SSE streaming, session management
├── graph.py                 # LangGraph StateGraph definition
├── models.py                # TypedDict state definitions
├── agents/
│   ├── file_profiler.py     # Pandas-based column profiling
│   ├── ddl_parser.py        # sqlglot DDL → TableSchema (LLM fallback)
│   ├── mapping_inference.py # LLM mapping inference
│   ├── human_review.py      # Interrupt node (pauses for user review)
│   └── data_migration.py    # SQLite migration + validation
├── static/
│   └── index.html           # Single-page UI (vanilla JS, no build step)
├── data/
│   └── raw/                 # Sample source files and target DDL for testing
├── requirements.txt         # Pinned dependencies (pip freeze output)
├── pyproject.toml           # uv / PEP 517 project metadata
└── .env                     # Local config (not committed)
```

---

## Sample data

`data/raw/` contains three source files and a multi-table DDL for end-to-end testing:

| File | Rows | Maps to |
|---|---|---|
| `crm_export.csv` | 200 | customers + contact_details + addresses |
| `legacy_users.json` | 150 | customers + contact_details + addresses |
| `subscriber_feed.csv` | 100 | contact_details + addresses |
| `target_schema.sql` | — | 3-table schema with FK relationships |

---

## Output

Each migration session produces a folder under `data/output/<session-id>/`:

```
data/output/<session-id>/
├── migration.db    # SQLite database with migrated data
└── report.json     # Full session metadata (mappings, row counts, validation)
```

Both files are downloadable from the Done panel in the UI.
