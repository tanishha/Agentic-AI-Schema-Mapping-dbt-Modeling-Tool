import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from agents.data_io import load_source_dataframe
from agents.data_migration import _db_path
from models import MigrationState, RawTableResult


def _identifier(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
    if not name:
        name = "field"
    if name[0].isdigit():
        name = f"_{name}"
    return name


def _dedupe_columns(columns: Iterable[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for column in columns:
        base = _identifier(column)
        count = seen.get(base, 0)
        seen[base] = count + 1
        result.append(base if count == 0 else f"{base}_{count + 1}")
    return result


def _raw_table_name(path: str) -> str:
    stem = Path(path).stem
    return "raw_" + _identifier(stem)


def _load_raw_table(db_path: str, source_path: str) -> RawTableResult:
    df = load_source_dataframe(source_path)
    df = df.copy()
    df.columns = _dedupe_columns(str(col) for col in df.columns)
    df = df.apply(lambda col: col.map(
        lambda value: json.dumps(value, sort_keys=True)
        if isinstance(value, (dict, list))
        else value
    ))
    df.insert(0, "_raw_row_number", range(1, len(df) + 1))
    df.insert(0, "_loaded_at", datetime.now(timezone.utc).isoformat())
    df.insert(0, "_source_file", os.path.basename(source_path))

    table = _raw_table_name(source_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        df.to_sql(table, conn, if_exists="replace", index=False)
    finally:
        conn.close()

    return RawTableResult(
        table=table,
        source_file=os.path.basename(source_path),
        rows=len(df),
        columns=[str(col) for col in df.columns],
        database=db_path,
    )


def raw_loader_agent(state: MigrationState) -> dict:
    db_path = _db_path(state["session_id"])
    events = list(state.get("events", []))
    raw_tables: list[RawTableResult] = []

    for source_path in state["source_files"]:
        result = _load_raw_table(db_path, source_path)
        raw_tables.append(result)
        events.append({
            "type": "raw_load_progress",
            "payload": {
                "table": result["table"],
                "file": result["source_file"],
                "rows": result["rows"],
                "columns": len(result["columns"]),
                "database": result["database"],
            },
        })

    events.append({
        "type": "raw_load_done",
        "payload": {
            "database": db_path,
            "tables": [
                {
                    "table": table["table"],
                    "file": table["source_file"],
                    "rows": table["rows"],
                    "columns": len(table["columns"]),
                }
                for table in raw_tables
            ],
        },
    })

    return {
        "raw_tables": raw_tables,
        "events": events,
    }
