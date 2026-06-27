# PRD: DBMapper Agentic Data Migration and dbt Workspace

**Version:** 2.0  
**Date:** 2026-06-27  
**Status:** Implemented / evolving

## 1. Product Summary

DBMapper is a local browser-based data-engineering workspace that uses deterministic Python/SQLite/dbt execution plus LLM-powered agents to move from raw CSV/JSON files to validated SQLite target tables and dbt transformation models.

The product supports first-time schema design, iterative schema extension, source-to-target mapping, human review, repeatable SQLite loading, ERD visualization, dbt model generation, dbt test generation, and dbt execution from a single UI.

## 2. Goals

| ID | Goal |
|---|---|
| G1 | Accept multiple CSV/JSON files per migration session |
| G2 | Support uploaded, generated, reused, and extended target schemas |
| G3 | Profile source files before any LLM mapping or schema generation |
| G4 | Validate DDL syntax and FK semantics before mapping |
| G5 | Let the user choose target tables before mapping |
| G6 | Use LLM agents to infer mappings, schemas, relationships, dbt plans, dbt SQL, and dbt tests |
| G7 | Keep human approval before database migration or dbt file application |
| G8 | Write per-session `migration.db` and persistent `project.db` |
| G9 | Show migration results, validation, ERD, and downloadable outputs |
| G10 | Provide dbt debug/compile/build/test from the UI |
| G11 | Preserve existing dbt SQL model files and backup before AI updates |

## 3. Non-Goals

- Authentication and multi-tenant security
- Cloud warehouse targets
- Production orchestration and scheduling
- Large-file distributed processing
- Automatic deployment to a remote dbt platform
- Full automatic data-contract governance

## 4. Current End-to-End Workflow

```text
Upload CSV/JSON files
  -> source profiling
  -> raw/session load
  -> schema upload/generation/reuse/extension
  -> DDL parse and SQLite validation
  -> target table selection
  -> LLM mapping inference
  -> human mapping review
  -> session/project SQLite migration
  -> validation + ERD + report
  -> dbt project preparation
  -> AI transformation preview
  -> human approval/apply
  -> dbt debug/compile/build/test
```

## 5. Schema Modes

| Mode | Description | Required files |
|---|---|---|
| Upload final schema SQL | User supplies existing target DDL | CSV/JSON + one SQL file |
| Generate final schema | AI generates SQLite DDL from source profiles | CSV/JSON only |
| Use `project.db` final schema | Server extracts existing final-table DDL from `project.db` | CSV/JSON only |
| Extend `project.db` final schema | AI proposes connected schema additions using current DB schema + new source profiles | CSV/JSON only |

Generated and extended schemas are editable. The user can manually change DDL or ask the schema agent for feedback-driven revisions before parsing and mapping.

## 6. Agentic AI Responsibilities

| Agent capability | Implementation area | Role |
|---|---|---|
| Source-aware schema generation | `agents/schema_generation.py` | Creates SQLite DDL from profiled files and sample-driven context |
| Schema extension | `agents/schema_generation.py` | Adds tables/columns/relationships while considering current `project.db` schema |
| Schema feedback loop | `agents/schema_generation.py` + UI chat | Applies user instructions to regenerate/refine DDL |
| Mapping inference | `agents/mapping_inference.py` | Maps target columns to source file/column candidates with confidence and reasons |
| Mapping sanitization | `agents/mapping_inference.py` | Rejects invented source columns and supports skipped generated integer PKs |
| Relationship reasoning | `agents/dbt_generation.py` | Uses declared/inferred FK metadata to plan joins and enriched marts |
| dbt transformation planning | `agents/dbt_generation.py` | Produces a transformation plan covering staging, marts, cleaning, joins, tests, assumptions |
| dbt SQL generation | `agents/dbt_generation.py` | Generates staging models, mart models, enriched relationship models, and `schema.yml` tests |
| dbt feedback loop | `agents/dbt_generation.py` + UI chat | Lets users request changes to generated dbt logic before/after apply |

The LLM is not used for low-level execution. File parsing, DDL validation, SQLite writes, dbt commands, and SQL script execution are deterministic. The LLM is used where semantic judgment is useful: schema design, mapping intent, relationship interpretation, transformation planning, and iterative refinement.

## 7. Human-in-the-Loop Controls

Human review is required at key decision points:

- Generated schema is shown before use.
- Schema feedback can be requested before parsing.
- Target tables must be selected before mapping.
- Proposed mappings can be edited before migration.
- Required mapping issues are shown as reasons rather than silently disabling migration.
- dbt transformations are previewed before files are applied.
- dbt feedback updates create a backup before writing.

## 8. Architecture

```text
Browser UI
  | REST + SSE
  v
FastAPI
  | session management
  | upload/download
  | dbt command endpoints
  v
LangGraph workflow
  | file_profiler
  | raw_loader
  | schema_generation / ddl_parser
  | table_selection
  | mapping_inference
  | human_review
  | data_migration
  v
SQLite + dbt
  | data/uploads/<session-id>/migration.db
  | data/database/project.db
  | data/dbt/
```

## 9. LangGraph Flow

```text
file_profiler
  -> raw_loader
  -> conditional:
       schema_mode=generate -> schema_generation -> schema_review -> ddl_parser
       schema_mode=extend   -> schema_generation -> schema_review -> ddl_parser
       schema_mode=upload   -> ddl_parser
       schema_mode=project  -> ddl_parser
  -> table_selection
  -> mapping_inference
  -> human_review
  -> data_migration
```

Interrupt/review points:

- Generated/extended schema review
- Target table selection
- Mapping review
- dbt transformation preview/apply outside LangGraph via FastAPI endpoints

## 10. Data Storage

| Store | Path | Lifecycle | Purpose |
|---|---|---|---|
| Upload folder | `data/uploads/<session-id>/` | Per session | Uploaded files, session DB, report |
| Session DB | `data/uploads/<session-id>/migration.db` | Recreated per migration | What loaded in one session |
| Project DB | `data/database/project.db` | Persistent | Shared accumulated/final database |
| Checkpoint DB | `data/checkpoints.db` | Persistent | LangGraph state |
| dbt project | `data/dbt/` | Persistent project artifact | dbt config, models, schema tests, plan |

SQLite has no credential layer in this project. Database access is file-based.

## 11. dbt Product Behavior

The dbt screen operates on existing `project.db` tables. It can be opened after migration or from the upload screen when data already exists.

| Action | Behavior |
|---|---|
| Refresh Table List | Reads current source/final tables and row counts from `project.db` |
| Prepare dbt Project | Refreshes dbt config/sources and creates missing starter files only |
| Transformations | Calls AI planning and returns a preview; does not write dbt files |
| Apply Transformations | Applies the pending preview, creates a backup, writes new/generated metadata, and preserves existing SQL models |
| Apply dbt Feedback | Sends user feedback to the LLM and writes returned dbt files after backup |
| dbt debug | Checks dbt profile/project/adapter setup |
| dbt compile | Compiles model graph and SQL |
| dbt build | Runs models and tests |
| dbt test | Runs tests only |
| Run All dbt Steps | Runs debug -> compile -> build -> test |

dbt model conventions:

- Staging models use `stg_` prefix.
- Mart models use `mart_` prefix.
- Enriched relationship marts use `mart_<child>_enriched`.
- Existing SQL model files are preserved by transformation sync.
- Manual models should live in `data/dbt/models/custom/`.

## 12. API Surface

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/sessions` | Create session |
| `POST` | `/sessions/{id}/upload` | Upload source/schema files |
| `POST` | `/sessions/{id}/start?schema_mode=...` | Start workflow |
| `GET` | `/sessions/{id}/events` | SSE progress |
| `GET` | `/sessions/{id}/schema-draft` | Return current DDL draft |
| `POST` | `/sessions/{id}/schema-feedback` | Refine DDL with AI feedback |
| `POST` | `/sessions/{id}/confirm-schema` | Parse/validate edited DDL |
| `POST` | `/sessions/{id}/select-tables` | Select target tables and infer mappings |
| `GET` | `/sessions/{id}/mappings` | Return proposed mappings |
| `PUT` | `/sessions/{id}/mappings/{mapping_id}` | Edit one mapping |
| `POST` | `/sessions/{id}/confirm` | Run migration |
| `POST` | `/sessions/{id}/remigrate` | Rerun migration after mapping edits |
| `GET` | `/sessions/{id}/download/db` | Download session DB |
| `GET` | `/sessions/{id}/download/report` | Download report |
| `GET` | `/sessions/{id}/download/schema` | Download session schema |
| `GET` | `/database/tables` | List current `project.db` tables |
| `POST` | `/database/dbt/prepare` | Prepare dbt project |
| `POST` | `/database/dbt/preview` | Preview AI dbt transformations |
| `POST` | `/database/dbt/apply` | Apply pending dbt preview |
| `POST` | `/database/dbt/feedback` | Apply AI dbt feedback update |
| `GET` | `/database/dbt/files` | List dbt files |
| `GET` | `/database/dbt/files/{path}` | Read one dbt file |
| `POST` | `/database/dbt/{command}` | Run `debug`, `compile`, `build`, or `test` |
| `POST` | `/database/dbt/run-all` | Run all dbt steps |

## 13. Validation Strategy

| Stage | Validation |
|---|---|
| Source profiling | CSV/JSON parse, inferred types, null rates, samples |
| DDL parse | SQL syntax, table/column extraction, PK/FK metadata |
| DDL validation | In-memory SQLite execution and FK reference validation |
| Mapping inference | Source existence checks, invented-column sanitization |
| Migration | Row counts, insert status, constraint errors |
| DB validation | FK checks, NOT NULL checks |
| dbt compile | SQL syntax and dependency graph validation |
| dbt build | Model execution and materialization |
| dbt test | not_null, unique, relationships tests |

## 14. Functional Requirements

| ID | Requirement |
|---|---|
| FR-01 | Accept CSV and JSON source files |
| FR-02 | Support uploaded/generated/project/extended schema modes |
| FR-03 | Profile files before schema generation and mapping |
| FR-04 | Validate DDL before table selection |
| FR-05 | Let users select target tables |
| FR-06 | Infer mappings with confidence and reason text |
| FR-07 | Let users edit mappings before migration |
| FR-08 | Allow generated integer PKs to be skipped |
| FR-09 | Show reasons when migration cannot proceed |
| FR-10 | Write session DB and project DB |
| FR-11 | Show row counts, validation, and ERD |
| FR-12 | Download DB/report/schema |
| FR-13 | Prepare dbt project from `project.db` |
| FR-14 | Preview dbt transformation changes before writing |
| FR-15 | Preserve existing dbt SQL models during transformation sync |
| FR-16 | Backup dbt files before AI feedback/apply changes |
| FR-17 | Run dbt debug/compile/build/test from UI |

## 15. Current Limitations

- SQLite is the only database target.
- dbt is local and uses `dbt-sqlite`.
- Raw/source mirroring into dbt sources is basic.
- dbt AI generation is conservative and relationship-driven, not a full semantic transformation compiler.
- AI-generated SQL still requires user review and dbt validation.
- Production orchestration is expected to be a later layer after this setup workspace.

## 16. Future Work

| Priority | Item |
|---|---|
| P1 | Add AI dbt debugger that reads failing dbt output and proposes patches |
| P1 | Add side-by-side diff for dbt preview and feedback updates |
| P2 | Add raw table preview and sample-data lineage |
| P2 | Add richer transformation expressions and lookup/key strategies |
| P2 | Add dbt documentation generation for models and columns |
| P3 | Add project export/package support |
| P3 | Add scheduler/orchestrator handoff for repeatable production runs |
| P3 | Add cloud warehouse adapters |

## 17. Product Positioning

DBMapper is best understood as an initial setup and iterative design workspace for AI-assisted data migration and transformation. It is not trying to replace production orchestration. Instead, it helps a data engineer quickly understand source files, design or extend a target schema, generate mapping logic, validate loads, generate dbt transformations, and establish reusable transformation assets that can later be moved into scheduled pipelines.
