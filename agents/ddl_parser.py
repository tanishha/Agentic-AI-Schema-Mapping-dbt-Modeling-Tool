import json
import os
import sqlite3
from typing import List, Tuple

import sqlglot
import sqlglot.expressions as exp
from llm_client import get_llm_client, get_model_name

from models import ForeignKey, TableSchema, TargetColumn, MigrationState


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _extract_fks(schema_expr, col_names: List[str]) -> List[ForeignKey]:
    """Extract foreign keys from inline and table-level REFERENCES constraints."""
    fks: List[ForeignKey] = []
    for col_def in schema_expr.find_all(exp.ColumnDef):
        col_name = col_def.name
        for constraint in col_def.find_all(exp.ColumnConstraint):
            if not isinstance(constraint.kind, exp.Reference):
                continue
            ref = constraint.kind
            try:
                ref_schema = ref.this
                ref_table = ref_schema.this.name
                ref_cols = [c.name for c in ref_schema.expressions]
                if ref_table and ref_cols:
                    fks.append(ForeignKey(
                        column=col_name,
                        ref_table=ref_table,
                        ref_column=ref_cols[0],
                    ))
            except Exception:
                pass

    for fk in schema_expr.find_all(exp.ForeignKey):
        try:
            fk_cols = [c.name if hasattr(c, "name") else c.this for c in fk.expressions]
            ref = fk.args.get("reference")
            ref_schema = ref.this if ref else None
            ref_table = ref_schema.this.name if ref_schema and ref_schema.this else None
            ref_cols = [c.name if hasattr(c, "name") else c.this for c in ref_schema.expressions]
            for idx, col_name in enumerate(fk_cols):
                ref_col = ref_cols[idx] if idx < len(ref_cols) else ref_cols[0]
                if col_name and ref_table and ref_col:
                    fks.append(ForeignKey(
                        column=col_name,
                        ref_table=ref_table,
                        ref_column=ref_col,
                    ))
        except Exception:
            pass

    return fks


def _parse_with_sqlglot(ddl: str) -> List[TableSchema]:
    statements = sqlglot.parse(ddl)
    tables: List[TableSchema] = []

    for stmt in statements:
        if not isinstance(stmt, exp.Create):
            continue
        table_node = stmt.find(exp.Table)
        if table_node is None:
            continue
        name = table_node.name

        # Collect PK columns from table-level PRIMARY KEY constraint
        pk_cols: set = set()
        for pk in stmt.find_all(exp.PrimaryKey):
            for col_id in pk.find_all(exp.Column):
                pk_cols.add(col_id.name.lower())

        schema_expr = stmt.find(exp.Schema)
        if schema_expr is None:
            continue

        columns: List[TargetColumn] = []
        for col_def in schema_expr.find_all(exp.ColumnDef):
            col_name = col_def.name
            dtype = col_def.find(exp.DataType)
            sql_type = dtype.sql(dialect="sqlite") if dtype else "TEXT"
            not_null = any(
                isinstance(c.kind, exp.NotNullColumnConstraint)
                for c in col_def.find_all(exp.ColumnConstraint)
            )
            is_pk = col_name.lower() in pk_cols or any(
                isinstance(c.kind, exp.PrimaryKeyColumnConstraint)
                for c in col_def.find_all(exp.ColumnConstraint)
            )
            columns.append(TargetColumn(
                name=col_name,
                sql_type=sql_type,
                nullable=not not_null and not is_pk,
                primary_key=is_pk,
            ))

        fks = _extract_fks(schema_expr, [c["name"] for c in columns])

        if columns:
            tables.append(TableSchema(name=name, columns=columns, foreign_keys=fks))

    if not tables:
        raise ValueError("No CREATE TABLE statements found")
    return tables


def validate_ddl(ddl: str, tables: List[TableSchema]) -> None:
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(ddl)
    except sqlite3.Error as exc:
        raise ValueError(f"SQLite DDL validation failed: {exc}") from exc
    finally:
        try:
            conn.close()
        except Exception:
            pass

    table_map = {table["name"].lower(): table for table in tables}
    for table in tables:
        column_map = {col["name"].lower(): col for col in table["columns"]}
        for fk in table.get("foreign_keys", []):
            if fk["column"].lower() not in column_map:
                raise ValueError(
                    f'Foreign key column {_quote_ident(table["name"])}.{_quote_ident(fk["column"])} does not exist'
                )
            ref_table = table_map.get(fk["ref_table"].lower())
            if not ref_table:
                raise ValueError(f'Foreign key references missing table {_quote_ident(fk["ref_table"])}')
            ref_cols = {col["name"].lower() for col in ref_table["columns"]}
            if fk["ref_column"].lower() not in ref_cols:
                raise ValueError(
                    f'Foreign key references missing column {_quote_ident(fk["ref_table"])}.{_quote_ident(fk["ref_column"])}'
                )


def _parse_with_llm(ddl: str) -> List[TableSchema]:
    client = get_llm_client()
    model = get_model_name(fast=True)

    prompt = (
        "Parse this SQL DDL. Return JSON only — no prose, no markdown fences.\n"
        'Schema: [{"name": "table_name", "columns": [{"name": "...", "sql_type": "...", '
        '"nullable": true|false, "primary_key": true|false}], '
        '"foreign_keys": [{"column": "...", "ref_table": "...", "ref_column": "..."}]}]\n\n'
        f"{ddl}"
    )
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    raw = response.choices[0].message.content.strip()
    if "```" in raw:
        parts = raw.split("```")
        for p in parts:
            s = p.strip().lstrip("json").strip()
            if s.startswith("["):
                raw = s
                break
    data = json.loads(raw)
    return [
        TableSchema(
            name=t["name"],
            columns=[TargetColumn(**c) for c in t["columns"]],
            foreign_keys=[ForeignKey(**fk) for fk in t.get("foreign_keys", [])],
        )
        for t in data
    ]


def ddl_parser_agent(state: MigrationState) -> dict:
    ddl = state["ddl_content"]
    events = list(state.get("events", []))

    try:
        tables = _parse_with_sqlglot(ddl)
    except Exception:
        tables = _parse_with_llm(ddl)
    validate_ddl(ddl, tables)

    events.append({
        "type": "ddl_parsed",
        "payload": {
            "tables": [{"name": t["name"], "columns": len(t["columns"])} for t in tables],
        },
    })
    return {
        "target_tables": tables,
        "stage": "MAPPING",
        "events": events,
    }
