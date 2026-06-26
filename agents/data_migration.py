import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import List

import pandas as pd

from agents.data_io import load_source_dataframe
from models import MappingItem, MigrationState, TableSchema, ValidationResult


def _session_dir(session_id: str) -> str:
    upload_root = os.getenv("UPLOAD_DIR", os.path.join("data", "uploads"))
    return os.path.join(upload_root, session_id)


def _project_db_path() -> str:
    path = os.getenv("MIGRATION_DB", os.path.join("data", "database", "project.db"))
    if path.lower().endswith(".db"):
        return path
    return os.path.join(path, "project.db")


def _db_path(session_id: str) -> str:
    return os.path.join(_session_dir(session_id), "migration.db")


def _report_path(session_id: str) -> str:
    return os.path.join(_session_dir(session_id), "report.json")


def _load_source_file(path: str) -> pd.DataFrame:
    return load_source_dataframe(path)


def _apply_mappings(df: pd.DataFrame, mappings: List[MappingItem], source_path: str) -> pd.DataFrame:
    filename = os.path.basename(source_path)
    file_maps = [
        m for m in mappings
        if m.get("source_file") == filename and m.get("source_column") is not None
    ]
    if not file_maps:
        return pd.DataFrame()

    result = pd.DataFrame()
    for m in file_maps:
        target = m["target_column"]
        source = m["source_column"]
        xform = m.get("transformation")
        if xform:
            try:
                result[target] = df.eval(xform)
            except Exception:
                result[target] = df[source] if source in df.columns else None
        else:
            result[target] = df[source] if source in df.columns else None
    return result


def validate_db(db_path: str, target_tables: List[TableSchema]) -> List[ValidationResult]:
    results: List[ValidationResult] = []
    conn = sqlite3.connect(db_path)
    try:
        for table in target_tables:
            name = table["name"]
            try:
                row_count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except Exception:
                results.append(ValidationResult(
                    table=name, row_count=0,
                    fk_violations=0, null_violations=0, status="error",
                ))
                continue

            fk_violations = sum(
                1 for _ in conn.execute(f'PRAGMA foreign_key_check("{name}")')
            )

            null_violations = 0
            for col in table["columns"]:
                if not col["nullable"] and not col["primary_key"]:
                    count = conn.execute(
                        f'SELECT COUNT(*) FROM "{name}" WHERE "{col["name"]}" IS NULL'
                    ).fetchone()[0]
                    null_violations += count

            status = "ok" if fk_violations == 0 and null_violations == 0 else "warning"
            results.append(ValidationResult(
                table=name,
                row_count=row_count,
                fk_violations=fk_violations,
                null_violations=null_violations,
                status=status,
            ))
    finally:
        conn.close()
    return results


def _ddl_create_if_missing(ddl: str) -> str:
    return re.sub(
        r"\bCREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS)",
        "CREATE TABLE IF NOT EXISTS ",
        ddl,
        flags=re.IGNORECASE,
    )


def _insert_dataframe(conn: sqlite3.Connection, table_name: str, df: pd.DataFrame, replace: bool):
    columns = [str(col) for col in df.columns]
    quoted_columns = ", ".join(f'"{col.replace(chr(34), chr(34) + chr(34))}"' for col in columns)
    placeholders = ", ".join("?" for _ in columns)
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    sql = f'{verb} INTO "{table_name.replace(chr(34), chr(34) + chr(34))}" ({quoted_columns}) VALUES ({placeholders})'
    clean = df.where(pd.notna(df), None)
    conn.executemany(sql, clean.itertuples(index=False, name=None))


def _load_database(
    db_path: str,
    ddl: str,
    target_tables: List[TableSchema],
    tables_to_load: List[TableSchema],
    mappings: List[MappingItem],
    source_files: List[str],
    events: list | None = None,
    recreate: bool = True,
    replace: bool = False,
) -> dict:
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    rows_loaded: dict = {}
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        if recreate:
            for table in reversed(target_tables):
                conn.execute(f'DROP TABLE IF EXISTS "{table["name"]}"')
            conn.executescript(ddl)
        else:
            conn.executescript(_ddl_create_if_missing(ddl))
        conn.commit()

        for table in tables_to_load:
            tname = table["name"]
            table_maps = [m for m in mappings if m.get("target_table") == tname]
            rows_loaded[tname] = {}

            for path in source_files:
                fname = os.path.basename(path)
                savepoint_open = False
                try:
                    df = _load_source_file(path)
                    transformed = _apply_mappings(df, table_maps, path)
                    if transformed.empty:
                        reason = "No selected mappings for this file and table"
                        rows_loaded[tname][fname] = {
                            "rows": 0,
                            "status": "skipped",
                            "reason": reason,
                        }
                        if events is not None:
                            events.append({"type": "migration_progress", "payload": {
                                "table": tname, "file": fname, "rows": 0, "skipped": True,
                                "reason": reason,
                            }})
                        continue
                    transformed = transformed.dropna(how="all")
                    if transformed.empty:
                        reason = "All transformed rows were empty"
                        rows_loaded[tname][fname] = {
                            "rows": 0,
                            "status": "skipped",
                            "reason": reason,
                        }
                        if events is not None:
                            events.append({"type": "migration_progress", "payload": {
                                "table": tname, "file": fname, "rows": 0, "skipped": True,
                                "reason": reason,
                            }})
                        continue
                    conn.execute("SAVEPOINT file_load")
                    savepoint_open = True
                    _insert_dataframe(conn, tname, transformed, replace=replace)
                    conn.execute("RELEASE SAVEPOINT file_load")
                    savepoint_open = False
                    count = len(transformed)
                    rows_loaded[tname][fname] = {
                        "rows": count,
                        "status": "loaded",
                        "reason": "",
                    }
                    if events is not None:
                        events.append({"type": "migration_progress", "payload": {
                            "table": tname, "file": fname, "rows": count,
                        }})
                except Exception as exc:
                    if savepoint_open:
                        try:
                            conn.execute("ROLLBACK TO SAVEPOINT file_load")
                            conn.execute("RELEASE SAVEPOINT file_load")
                        except Exception:
                            pass
                    rows_loaded[tname][fname] = {
                        "rows": 0,
                        "status": "error",
                        "reason": str(exc),
                    }
                    if events is not None:
                        events.append({"type": "migration_progress", "payload": {
                            "table": tname, "file": fname, "rows": 0, "error": str(exc),
                        }})
        conn.commit()
    finally:
        conn.close()
    return rows_loaded


def data_migration_agent(state: MigrationState) -> dict:
    session_id = state["session_id"]
    mappings = state["confirmed_mappings"]
    source_files = state["source_files"]
    target_tables = state["target_tables"]
    selected = set(state.get("selected_tables", []))
    tables_to_load = [
        table for table in target_tables
        if not selected or table["name"] in selected
    ]
    ddl = state["ddl_content"]
    events = list(state.get("events", []))
    session_db = _db_path(session_id)
    project_db = _project_db_path()

    rows_loaded = _load_database(
        session_db,
        ddl,
        target_tables,
        tables_to_load,
        mappings,
        source_files,
        events,
        recreate=True,
        replace=False,
    )
    _load_database(
        project_db,
        ddl,
        target_tables,
        tables_to_load,
        mappings,
        source_files,
        None,
        recreate=False,
        replace=True,
    )

    validation = validate_db(session_db, tables_to_load)

    total = sum(
        detail["rows"] if isinstance(detail, dict) else detail
        for file_counts in rows_loaded.values()
        for detail in file_counts.values()
    )
    events.append({"type": "done", "payload": {"total_rows": total}})

    # Persist report
    report = {
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_database": session_db,
        "project_database": project_db,
        "source_files": [os.path.basename(p) for p in source_files],
        "target_tables": target_tables,
        "selected_tables": [t["name"] for t in tables_to_load],
        "mappings": [dict(m) for m in mappings],
        "rows_loaded": rows_loaded,
        "validation": validation,
    }
    os.makedirs(_session_dir(session_id), exist_ok=True)
    with open(_report_path(session_id), "w") as f:
        json.dump(report, f, indent=2)

    return {
        "rows_loaded": rows_loaded,
        "validation_results": validation,
        "stage": "DONE",
        "events": events,
    }
