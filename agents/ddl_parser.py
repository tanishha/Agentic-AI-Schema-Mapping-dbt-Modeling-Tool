import json
import os
from typing import List, Tuple

import sqlglot
import sqlglot.expressions as exp
from llm_client import get_llm_client, get_model_name

from models import ForeignKey, TableSchema, TargetColumn, MigrationState


def _extract_fks(schema_expr, col_names: List[str]) -> List[ForeignKey]:
    """Extract foreign keys from inline REFERENCES constraints."""
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
            sql_type = dtype.sql() if dtype else "TEXT"
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
