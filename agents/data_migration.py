import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import List

import pandas as pd

from models import MappingItem, MigrationState, TableSchema, ValidationResult


def _output_dir(session_id: str) -> str:
    return os.path.join("data", "output", session_id)


def _db_path(session_id: str) -> str:
    return os.path.join(_output_dir(session_id), "migration.db")


def _load_source_file(path: str) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return pd.read_csv(path)
    if ext == ".json":
        try:
            return pd.read_json(path, orient="records", lines=False)
        except ValueError:
            return pd.read_json(path, orient="records", lines=True)
    raise ValueError(f"Unsupported file: {path}")


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


def data_migration_agent(state: MigrationState) -> dict:
    session_id = state["session_id"]
    mappings = state["confirmed_mappings"]
    source_files = state["source_files"]
    target_tables = state["target_tables"]
    ddl = state["ddl_content"]
    events = list(state.get("events", []))
    rows_loaded: dict = {}

    out_dir = _output_dir(session_id)
    os.makedirs(out_dir, exist_ok=True)
    db = _db_path(session_id)

    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        # Drop existing tables (reverse order to respect FKs) then recreate
        for table in reversed(target_tables):
            conn.execute(f'DROP TABLE IF EXISTS "{table["name"]}"')
        conn.executescript(ddl)
        conn.commit()

        for table in target_tables:
            tname = table["name"]
            table_maps = [m for m in mappings if m.get("target_table") == tname]
            rows_loaded[tname] = {}

            for path in source_files:
                fname = os.path.basename(path)
                try:
                    df = _load_source_file(path)
                    transformed = _apply_mappings(df, table_maps, path)
                    if transformed.empty:
                        rows_loaded[tname][fname] = 0
                        events.append({"type": "migration_progress", "payload": {
                            "table": tname, "file": fname, "rows": 0, "skipped": True,
                        }})
                        continue
                    transformed = transformed.dropna(how="all")
                    transformed.to_sql(tname, conn, if_exists="append", index=False)
                    count = len(transformed)
                    rows_loaded[tname][fname] = count
                    events.append({"type": "migration_progress", "payload": {
                        "table": tname, "file": fname, "rows": count,
                    }})
                except Exception as exc:
                    rows_loaded[tname][fname] = 0
                    events.append({"type": "migration_progress", "payload": {
                        "table": tname, "file": fname, "rows": 0, "error": str(exc),
                    }})
        conn.commit()
    finally:
        conn.close()

    validation = validate_db(db, target_tables)

    total = sum(
        cnt for file_counts in rows_loaded.values()
        for cnt in file_counts.values()
    )
    events.append({"type": "done", "payload": {"total_rows": total}})

    # Persist report
    report = {
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_files": [os.path.basename(p) for p in source_files],
        "target_tables": target_tables,
        "mappings": [dict(m) for m in mappings],
        "rows_loaded": rows_loaded,
        "validation": validation,
    }
    with open(os.path.join(out_dir, "report.json"), "w") as f:
        json.dump(report, f, indent=2)

    return {
        "rows_loaded": rows_loaded,
        "validation_results": validation,
        "stage": "DONE",
        "events": events,
    }
