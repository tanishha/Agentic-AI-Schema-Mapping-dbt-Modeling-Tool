import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv

load_dotenv()
load_dotenv()
print("=== ENV DEBUG ===")
print("cwd:", os.getcwd())
print("LLM_PROVIDER:", repr(os.getenv("LLM_PROVIDER")))
print("LLM_MODEL:", repr(os.getenv("LLM_MODEL")))
print("AZURE_OPENAI_DEPLOYMENT:", repr(os.getenv("AZURE_OPENAI_DEPLOYMENT")))
print("==================")

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents.data_migration import data_migration_agent, validate_db, _db_path, _report_path
from agents.mapping_inference import mapping_inference_agent
from graph import compiled_graph
from models import MappingItem, MigrationState

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "data/uploads")

app = FastAPI(title="DBMapper")
app.mount("/static", StaticFiles(directory="static"), name="static")

_sse_queues: dict[str, asyncio.Queue] = {}


# ── helpers ───────────────────────────────────────────────────────────────────

def _session_upload_dir(session_id: str) -> Path:
    return Path(UPLOAD_DIR) / session_id


def _safe_path(session_id: str, filename: str) -> Path:
    base = _session_upload_dir(session_id).resolve()
    target = (base / filename).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=400, detail="Path traversal detected")
    return target


def _graph_config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def _get_state(session_id: str) -> MigrationState:
    snapshot = compiled_graph.get_state(_graph_config(session_id))
    if snapshot is None or not snapshot.values:
        raise HTTPException(status_code=404, detail="Session not found")
    return snapshot.values


async def _run_graph_and_enqueue(session_id: str, initial_state: dict):
    queue = _sse_queues.setdefault(session_id, asyncio.Queue())
    loop = asyncio.get_event_loop()

    def _invoke():
        events_seen = 0
        for chunk in compiled_graph.stream(
            initial_state, _graph_config(session_id), stream_mode="values"
        ):
            new_events = chunk.get("events", [])[events_seen:]
            for ev in new_events:
                loop.call_soon_threadsafe(queue.put_nowait, ev)
            events_seen += len(new_events)

    await loop.run_in_executor(None, _invoke)
    queue.put_nowait(None)


async def _do_migrate_and_enqueue(session_id: str, confirmed_mappings: list):
    """Run data migration directly — no LangGraph resume, so no stale-event replay.
    Used for both first-time confirm and re-migrate after editing mappings."""
    queue = _sse_queues[session_id]  # caller must set a fresh queue first
    state = dict(_get_state(session_id))
    state["confirmed_mappings"] = confirmed_mappings
    loop = asyncio.get_event_loop()

    def _run():
        prior_event_count = len(state.get("events", []))
        result = data_migration_agent(state)
        for ev in result.get("events", [])[prior_event_count:]:
            loop.call_soon_threadsafe(queue.put_nowait, ev)
        compiled_graph.update_state(
            _graph_config(session_id),
            {
                "confirmed_mappings": confirmed_mappings,
                "rows_loaded": result["rows_loaded"],
                "validation_results": result["validation_results"],
                "stage": "DONE",
            },
        )

    await loop.run_in_executor(None, _run)
    queue.put_nowait(None)


async def _do_mapping_and_enqueue(session_id: str, selected_tables: list[str]):
    queue = _sse_queues[session_id]
    state = dict(_get_state(session_id))
    state["selected_tables"] = selected_tables
    state["events"] = []
    loop = asyncio.get_event_loop()

    def _run():
        result = mapping_inference_agent(state)
        for ev in result.get("events", []):
            loop.call_soon_threadsafe(queue.put_nowait, ev)
        compiled_graph.update_state(
            _graph_config(session_id),
            {
                "selected_tables": selected_tables,
                "proposed_mappings": result["proposed_mappings"],
                "stage": "REVIEWING",
            },
        )

    await loop.run_in_executor(None, _run)
    queue.put_nowait(None)


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse("static/index.html")


class SessionOut(BaseModel):
    session_id: str


@app.post("/sessions", response_model=SessionOut)
async def create_session():
    session_id = str(uuid.uuid4())
    _session_upload_dir(session_id).mkdir(parents=True, exist_ok=True)
    return SessionOut(session_id=session_id)


@app.post("/sessions/{session_id}/upload")
async def upload_files(session_id: str, files: list[UploadFile] = File(...)):
    upload_dir = _session_upload_dir(session_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        dest = _safe_path(session_id, f.filename)
        dest.write_bytes(await f.read())
        saved.append(f.filename)
    return {"files": saved}


@app.post("/sessions/{session_id}/start")
async def start_session(session_id: str):
    upload_dir = _session_upload_dir(session_id)
    if not upload_dir.exists():
        raise HTTPException(404, "Session not found")

    all_files = list(upload_dir.iterdir())
    data_files = [str(f) for f in all_files if f.suffix.lower() in (".csv", ".json")]
    ddl_files = [f for f in all_files if f.suffix.lower() == ".sql"]

    if len(ddl_files) != 1:
        raise HTTPException(400, "Exactly one .sql DDL file required")
    if not data_files:
        raise HTTPException(400, "At least one data file required")
    initial_state: MigrationState = {
        "session_id": session_id,
        "stage": "PROFILING",
        "source_files": data_files,
        "ddl_content": ddl_files[0].read_text(),
        "intermediate_catalog": [],
        "target_tables": [],
        "selected_tables": [],
        "proposed_mappings": [],
        "confirmed_mappings": [],
        "rows_loaded": {},
        "validation_results": [],
        "error": None,
        "events": [],
    }
    asyncio.create_task(_run_graph_and_enqueue(session_id, initial_state))
    return {"status": "started"}


async def _sse_generator(session_id: str) -> AsyncGenerator[str, None]:
    queue = _sse_queues.setdefault(session_id, asyncio.Queue())
    while True:
        event = await queue.get()
        if event is None:
            yield 'data: {"type":"stream_end"}\n\n'
            break
        yield f"data: {json.dumps(event)}\n\n"


@app.get("/sessions/{session_id}/events")
async def sse_events(session_id: str):
    return StreamingResponse(
        _sse_generator(session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/sessions/{session_id}/mappings")
async def get_mappings(session_id: str):
    state = _get_state(session_id)
    return {"mappings": state.get("proposed_mappings", [])}


@app.get("/sessions/{session_id}/schema")
async def get_schema(session_id: str):
    state = _get_state(session_id)
    return {
        "tables": state.get("target_tables", []),
        "selected_tables": state.get("selected_tables", []),
    }


@app.get("/sessions/{session_id}/catalog")
async def get_catalog(session_id: str):
    state = _get_state(session_id)
    return {"catalog": state.get("intermediate_catalog", [])}


class TableSelectionBody(BaseModel):
    tables: list[str]


@app.post("/sessions/{session_id}/select-tables")
async def select_tables(session_id: str, body: TableSelectionBody):
    state = _get_state(session_id)
    available = {table["name"] for table in state.get("target_tables", [])}
    selected = [name for name in body.tables if name in available]
    if not selected:
        raise HTTPException(400, "Select at least one table from the target schema")

    _sse_queues[session_id] = asyncio.Queue()
    asyncio.create_task(_do_mapping_and_enqueue(session_id, selected))
    return {"status": "mapping", "selected_tables": selected}


class MappingUpdate(BaseModel):
    source_file: str | None = None
    source_column: str | None = None
    transformation: str | None = None


@app.put("/sessions/{session_id}/mappings/{mapping_id}")
async def update_mapping(session_id: str, mapping_id: str, body: MappingUpdate):
    state = _get_state(session_id)
    mappings = list(state.get("proposed_mappings", []))
    for i, m in enumerate(mappings):
        if m["id"] == mapping_id:
            mappings[i] = {**m, "source_file": body.source_file,
                           "source_column": body.source_column,
                           "transformation": body.transformation}
            compiled_graph.update_state(_graph_config(session_id),
                                        {"proposed_mappings": mappings})
            return {"updated": mapping_id}
    raise HTTPException(404, "Mapping not found")


class ConfirmBody(BaseModel):
    mappings: list[dict]


@app.post("/sessions/{session_id}/confirm")
async def confirm_mappings(session_id: str, body: ConfirmBody):
    _sse_queues[session_id] = asyncio.Queue()  # fresh queue before task starts
    confirmed = [MappingItem(**m) for m in body.mappings]
    asyncio.create_task(_do_migrate_and_enqueue(session_id, confirmed))
    return {"status": "migrating"}


@app.post("/sessions/{session_id}/remigrate")
async def remigrate(session_id: str, body: ConfirmBody):
    _sse_queues[session_id] = asyncio.Queue()
    confirmed = [MappingItem(**m) for m in body.mappings]
    asyncio.create_task(_do_migrate_and_enqueue(session_id, confirmed))
    return {"status": "migrating"}


@app.get("/sessions/{session_id}/validate")
async def validate_session(session_id: str):
    state = _get_state(session_id)
    db = _db_path(session_id)
    if not os.path.exists(db):
        raise HTTPException(404, "Migration DB not found — run migration first")
    target_tables = state.get("target_tables", [])
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, lambda: validate_db(db, target_tables))
    compiled_graph.update_state(_graph_config(session_id), {"validation_results": results})
    return {"validation": results}


@app.get("/sessions/{session_id}/result")
async def get_result(session_id: str):
    state = _get_state(session_id)
    return {
        "stage": state.get("stage"),
        "rows_loaded": state.get("rows_loaded", {}),
        "validation_results": state.get("validation_results", []),
        "error": state.get("error"),
    }


@app.get("/sessions/{session_id}/download/db")
async def download_db(session_id: str):
    db = _db_path(session_id)
    if not os.path.exists(db):
        raise HTTPException(404, "Migration DB not ready")
    return FileResponse(db, media_type="application/octet-stream", filename="migration.db")


@app.get("/sessions/{session_id}/download/report")
async def download_report(session_id: str):
    path = _report_path(session_id)
    if not os.path.exists(path):
        raise HTTPException(404, "Report not ready")
    return FileResponse(path, media_type="application/json", filename="migration_report.json")


@app.post("/sessions/{session_id}/reset")
async def reset_session(session_id: str):
    upload_dir = _session_upload_dir(session_id)
    if upload_dir.exists():
        shutil.rmtree(upload_dir)
    _sse_queues.pop(session_id, None)
    return {"status": "reset"}
