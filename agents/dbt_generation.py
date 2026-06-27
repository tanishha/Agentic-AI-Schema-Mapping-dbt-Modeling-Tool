import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llm_client import get_llm_client, get_model_name


def _identifier(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
    if not name:
        name = "model"
    if name[0].isdigit():
        name = f"_{name}"
    return name


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _read_schema(db_path: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    try:
        table_names = [
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                  AND name NOT LIKE 'stg_%'
                  AND name NOT LIKE 'mart_%'
                ORDER BY name
                """
            ).fetchall()
        ]
        tables = []
        for table in table_names:
            columns = []
            for row in conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall():
                columns.append({
                    "name": row[1],
                    "type": row[2] or "TEXT",
                    "not_null": bool(row[3]),
                    "default": row[4],
                    "primary_key": bool(row[5]),
                })
            explicit_fks = [
                {"column": row[3], "ref_table": row[2], "ref_column": row[4]}
                for row in conn.execute(f"PRAGMA foreign_key_list({_quote_identifier(table)})").fetchall()
            ]
            tables.append({"name": table, "columns": columns, "foreign_keys": explicit_fks})
        return tables
    finally:
        conn.close()


def _infer_relationships(tables: list[dict[str, Any]]) -> list[dict[str, str]]:
    pk_by_table = {
        table["name"]: next((col["name"] for col in table["columns"] if col["primary_key"]), None)
        for table in tables
    }
    relationships: list[dict[str, str]] = []
    seen = set()

    for table in tables:
        for fk in table.get("foreign_keys", []):
            key = (table["name"], fk["column"], fk["ref_table"], fk["ref_column"])
            seen.add(key)
            relationships.append({
                "child_table": table["name"],
                "child_column": fk["column"],
                "parent_table": fk["ref_table"],
                "parent_column": fk["ref_column"],
                "source": "declared_fk",
            })

    for table in tables:
        for column in table["columns"]:
            col_name = column["name"]
            if not col_name.endswith("_id"):
                continue
            if column["primary_key"]:
                continue
            base = col_name[:-3]
            for parent, pk in pk_by_table.items():
                if parent == table["name"] or not pk:
                    continue
                parent_base = parent.rstrip("s")
                if col_name == pk or base == parent_base or base == parent:
                    key = (table["name"], col_name, parent, pk)
                    if key not in seen:
                        seen.add(key)
                        relationships.append({
                            "child_table": table["name"],
                            "child_column": col_name,
                            "parent_table": parent,
                            "parent_column": pk,
                            "source": "inferred_name_match",
                        })
                    break

    return relationships


def _dbt_sql_type(sql_type: str) -> str:
    upper = (sql_type or "").upper()
    if "INT" in upper:
        return "integer"
    if any(token in upper for token in ("REAL", "FLOA", "DOUB", "DEC", "NUM")):
        return "real"
    return "text"


def _staging_expression(column: dict[str, Any]) -> str:
    source = _quote_identifier(column["name"])
    alias = _quote_identifier(column["name"])
    kind = _dbt_sql_type(column["type"])
    if kind == "integer":
        return f"cast({source} as integer) as {alias}"
    if kind == "real":
        return f"cast({source} as real) as {alias}"
    return f"nullif(trim(cast({source} as text)), '') as {alias}"


def _build_fallback_plan(tables: list[dict[str, Any]]) -> dict[str, Any]:
    relationships = _infer_relationships(tables)
    child_tables = sorted({rel["child_table"] for rel in relationships})
    return {
        "source": "fallback",
        "summary": "Generated dbt staging cleanup models, final mart pass-through models, tests, and relationship-based enriched marts from project.db metadata.",
        "tables": [
            {
                "name": table["name"],
                "staging_model": f"stg_{_identifier(table['name'])}",
                "mart_model": f"mart_{_identifier(table['name'])}",
                "cleaning": [
                    {
                        "column": col["name"],
                        "logic": "cast numeric fields and trim empty strings from text fields",
                    }
                    for col in table["columns"]
                ],
            }
            for table in tables
        ],
        "relationships": relationships,
        "derived_models": [
            {
                "name": f"mart_{_identifier(child)}_enriched",
                "logic": "Left join child table to all inferred parent tables.",
            }
            for child in child_tables
        ],
        "tests": "Primary keys get unique/not_null tests. Inferred foreign keys get relationships tests.",
    }


def _build_ai_prompt(tables: list[dict[str, Any]], fallback_plan: dict[str, Any]) -> str:
    return (
        "Create a dbt transformation plan for these SQLite tables.\n"
        "Return JSON only, no markdown. Do not invent source tables or columns.\n"
        "The implementation will still generate conservative SQL, so the plan should explain joins, cleaning, derived fields, tests, and assumptions.\n\n"
        "Required JSON keys: summary, tables, relationships, derived_models, tests, assumptions.\n\n"
        f"SQLite schema:\n{json.dumps(tables, indent=2)}\n\n"
        f"Fallback relationship analysis:\n{json.dumps(fallback_plan, indent=2)}"
    )


def _ai_plan(tables: list[dict[str, Any]], fallback_plan: dict[str, Any]) -> dict[str, Any]:
    try:
        client = get_llm_client()
        response = client.chat.completions.create(
            model=get_model_name(),
            messages=[{"role": "user", "content": _build_ai_prompt(tables, fallback_plan)}],
            temperature=0,
        )
        text = response.choices[0].message.content.strip()
        if "```" in text:
            for part in text.split("```"):
                part = part.strip()
                if part.lower().startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    text = part
                    break
        plan = json.loads(text)
        plan["source"] = "llm"
        return plan
    except Exception as exc:
        plan = dict(fallback_plan)
        plan["source"] = f"fallback: {exc}"
        return plan


def _write(path: Path, content: str, written: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    written.append(str(path))


def _write_if_missing(path: Path, content: str, written: list[str], skipped: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        skipped.append(str(path))
        return
    path.write_text(content.strip() + "\n", encoding="utf-8")
    written.append(str(path))


def backup_dbt_project(dbt_dir: str, reason: str) -> str | None:
    base = Path(dbt_dir)
    if not base.exists():
        return None

    files = [
        path for path in base.rglob("*")
        if path.is_file()
        and "target" not in path.relative_to(base).parts
        and "logs" not in path.relative_to(base).parts
        and "backups" not in path.relative_to(base).parts
        and not any(part.startswith(".") for part in path.relative_to(base).parts)
    ]
    if not files:
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_reason = _identifier(reason)
    backup_dir = base / "backups" / f"{stamp}_{safe_reason}"
    for path in files:
        rel = path.relative_to(base)
        dest = backup_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    return str(backup_dir)


def _strip_json_markdown(text: str) -> str:
    if "```" not in text:
        return text.strip()
    for part in text.split("```"):
        stripped = part.strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
        if stripped.startswith("{"):
            return stripped
    return text.strip()


def _collect_dbt_files(dbt_dir: str) -> list[dict[str, str]]:
    base = Path(dbt_dir)
    candidates = [
        base / "dbt_project.yml",
        base / "profiles.yml",
        base / "transformation_plan.json",
    ]
    candidates.extend((base / "models").rglob("*.sql"))
    candidates.extend((base / "models").rglob("*.yml"))

    files = []
    for path in candidates:
        if path.exists() and path.is_file():
            rel = path.relative_to(base).as_posix()
            files.append({
                "path": rel,
                "content": path.read_text(encoding="utf-8"),
            })
    return sorted(files, key=lambda item: item["path"])


def _safe_dbt_path(dbt_dir: str, relative_path: str) -> Path:
    rel = Path(relative_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"Unsafe dbt file path: {relative_path}")
    if rel.suffix.lower() not in {".sql", ".yml", ".yaml", ".json"}:
        raise ValueError(f"Unsupported dbt file type: {relative_path}")
    target = (Path(dbt_dir) / rel).resolve()
    base = Path(dbt_dir).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError(f"Unsafe dbt file path: {relative_path}")
    return target


def _feedback_prompt(
    tables: list[dict[str, Any]],
    current_files: list[dict[str, str]],
    feedback: str,
) -> str:
    trimmed_files = [
        {
            "path": item["path"],
            "content": item["content"][:12000],
        }
        for item in current_files
    ]
    return (
        "Revise this dbt project based on the user's feedback.\n"
        "Return JSON only, no markdown fences.\n\n"
        "Rules:\n"
        "- Do not invent source tables or source columns not present in the SQLite schema.\n"
        "- Keep SQL compatible with SQLite and dbt.\n"
        "- Use source('project', table_name) only in staging models.\n"
        "- Use ref() between dbt models.\n"
        "- Return only files that should be created or replaced.\n"
        "- File paths must be relative to the dbt project directory and stay under models/, or be transformation_plan.json.\n\n"
        "Required JSON shape:\n"
        "{\n"
        '  "message": "short explanation",\n'
        '  "plan": { "summary": "...", "assumptions": [] },\n'
        '  "files": [ { "path": "models/marts/example.sql", "content": "select ..." } ]\n'
        "}\n\n"
        f"SQLite schema:\n{json.dumps(tables, indent=2)}\n\n"
        f"Current dbt files:\n{json.dumps(trimmed_files, indent=2)}\n\n"
        f"User feedback:\n{feedback}"
    )


def _staging_sql(table: dict[str, Any]) -> str:
    expressions = ",\n        ".join(_staging_expression(col) for col in table["columns"])
    return f"""
with source as (
    select * from {{{{ source('project', '{table["name"]}') }}}}
),
cleaned as (
    select
        {expressions}
    from source
)
select * from cleaned
    """


def _mart_sql(table: dict[str, Any]) -> str:
    model = _identifier(table["name"])
    return f"""
select
    *
from {{{{ ref('stg_{model}') }}}}
    """


def _enriched_sql(child: str, relationships: list[dict[str, str]], tables: list[dict[str, Any]]) -> str:
    child_model = _identifier(child)
    child_cols = next(t["columns"] for t in tables if t["name"] == child)
    child_select = [
        f"    child.{_quote_identifier(col['name'])} as {_quote_identifier(col['name'])}"
        for col in child_cols
    ]
    parent_select: list[str] = []
    joins: list[str] = []
    for idx, rel in enumerate(relationships, start=1):
        parent = rel["parent_table"]
        parent_model = _identifier(parent)
        alias = f"parent_{idx}"
        parent_cols = next(t["columns"] for t in tables if t["name"] == parent)
        for col in parent_cols:
            if col["name"] == rel["parent_column"]:
                continue
            parent_select.append(
                f"    {alias}.{_quote_identifier(col['name'])} as {_quote_identifier(parent_model + '_' + col['name'])}"
            )
        joins.append(
            f"""left join {{{{ ref('stg_{parent_model}') }}}} as {alias}
    on child.{_quote_identifier(rel["child_column"])} = {alias}.{_quote_identifier(rel["parent_column"])}"""
        )
    select_list = ",\n".join(child_select + parent_select)
    join_sql = "\n".join(joins)
    return f"""
select
{select_list}
from {{{{ ref('stg_{child_model}') }}}} as child
{join_sql}
    """


def _schema_yml(tables: list[dict[str, Any]], relationships: list[dict[str, str]]) -> str:
    lines = ["version: 2", "", "models:"]
    rel_by_child = {}
    for rel in relationships:
        rel_by_child.setdefault((rel["child_table"], rel["child_column"]), []).append(rel)

    for table in tables:
        model = _identifier(table["name"])
        lines.extend([
            f"  - name: stg_{model}",
            f"    description: Cleaned staging model for {table['name']}.",
            "    columns:",
        ])
        for col in table["columns"]:
            lines.append(f"      - name: {col['name']}")
            tests = []
            if col["primary_key"]:
                tests.extend(["not_null", "unique"])
            elif col["not_null"]:
                tests.append("not_null")
            for rel in rel_by_child.get((table["name"], col["name"]), []):
                tests.append({
                    "relationships": {
                        "to": f"ref('stg_{_identifier(rel['parent_table'])}')",
                        "field": rel["parent_column"],
                    }
                })
            if tests:
                lines.append("        tests:")
                for test in tests:
                    if isinstance(test, str):
                        lines.append(f"          - {test}")
                    else:
                        rel = test["relationships"]
                        lines.append("          - relationships:")
                        lines.append("              arguments:")
                        lines.append(f"                to: {rel['to']}")
                        lines.append(f"                field: {rel['field']}")
        lines.extend([
            f"  - name: mart_{model}",
            f"    description: Final mart model for {table['name']}.",
        ])
    return "\n".join(lines)


def _planned_dbt_files(
    dbt_dir: str,
    tables: list[dict[str, Any]],
    plan: dict[str, Any],
    relationships: list[dict[str, str]],
) -> list[dict[str, str]]:
    base = Path(dbt_dir)
    staging_dir = base / "models" / "staging"
    marts_dir = base / "models" / "marts"
    files = [
        {
            "path": "transformation_plan.json",
            "content": json.dumps(plan, indent=2),
            "action": "update",
        },
        {
            "path": "models/schema.yml",
            "content": _schema_yml(tables, relationships),
            "action": "update",
        },
    ]

    relationships_by_child: dict[str, list[dict[str, str]]] = {}
    for rel in relationships:
        relationships_by_child.setdefault(rel["child_table"], []).append(rel)

    for table in tables:
        model = _identifier(table["name"])
        staging_path = staging_dir / f"stg_{model}.sql"
        mart_path = marts_dir / f"mart_{model}.sql"
        files.append({
            "path": f"models/staging/stg_{model}.sql",
            "content": _staging_sql(table),
            "action": "preserve" if staging_path.exists() else "create",
        })
        files.append({
            "path": f"models/marts/mart_{model}.sql",
            "content": _mart_sql(table),
            "action": "preserve" if mart_path.exists() else "create",
        })

    for child, child_relationships in relationships_by_child.items():
        name = f"mart_{_identifier(child)}_enriched"
        path = marts_dir / f"{name}.sql"
        files.append({
            "path": f"models/marts/{name}.sql",
            "content": _enriched_sql(child, child_relationships, tables),
            "action": "preserve" if path.exists() else "create",
        })

    return files


def preview_dbt_transformations(db_path: str, dbt_dir: str) -> dict[str, Any]:
    tables = _read_schema(db_path)
    if not tables:
        raise ValueError("project.db has no user tables")

    fallback_plan = _build_fallback_plan(tables)
    plan = _ai_plan(tables, fallback_plan)
    relationships = fallback_plan["relationships"]
    files = _planned_dbt_files(dbt_dir, tables, plan, relationships)
    payload = {
        "dbt_path": dbt_dir,
        "plan": plan,
        "relationships": relationships,
        "file_actions": [{"path": item["path"], "action": item["action"]} for item in files],
        "files": files,
    }
    pending_path = Path(dbt_dir) / ".pending_transformations.json"
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {key: value for key, value in payload.items() if key != "files"}


def apply_pending_dbt_transformations(db_path: str, dbt_dir: str) -> dict[str, Any]:
    pending_path = Path(dbt_dir) / ".pending_transformations.json"
    if pending_path.exists():
        payload = json.loads(pending_path.read_text(encoding="utf-8"))
    else:
        preview_dbt_transformations(db_path, dbt_dir)
        payload = json.loads(pending_path.read_text(encoding="utf-8"))

    written: list[str] = []
    skipped: list[str] = []
    backup_path = backup_dbt_project(dbt_dir, "apply_transformations")

    for item in payload.get("files", []):
        path = _safe_dbt_path(dbt_dir, item["path"])
        content = item.get("content", "")
        action = item.get("action")
        if action == "preserve" and path.exists():
            skipped.append(str(path))
            continue
        _write(path, content, written)

    pending_path.unlink(missing_ok=True)
    return {
        "dbt_path": dbt_dir,
        "plan": payload.get("plan", {}),
        "relationships": payload.get("relationships", []),
        "files": written,
        "skipped_files": skipped,
        "backup_path": backup_path,
        "file_actions": payload.get("file_actions", []),
    }


def generate_dbt_transformations(db_path: str, dbt_dir: str) -> dict[str, Any]:
    preview_dbt_transformations(db_path, dbt_dir)
    return apply_pending_dbt_transformations(db_path, dbt_dir)


def refine_dbt_transformations(db_path: str, dbt_dir: str, feedback: str) -> dict[str, Any]:
    if not feedback.strip():
        raise ValueError("Feedback cannot be empty")

    tables = _read_schema(db_path)
    if not tables:
        raise ValueError("project.db has no user tables")

    current_files = _collect_dbt_files(dbt_dir)
    if not current_files:
        generate_dbt_transformations(db_path, dbt_dir)
        current_files = _collect_dbt_files(dbt_dir)

    try:
        client = get_llm_client()
        response = client.chat.completions.create(
            model=get_model_name(),
            messages=[{"role": "user", "content": _feedback_prompt(tables, current_files, feedback)}],
            temperature=0,
        )
        payload = json.loads(_strip_json_markdown(response.choices[0].message.content))
    except Exception as exc:
        raise ValueError(f"dbt feedback requires a working LLM configuration: {exc}") from exc

    backup_path = backup_dbt_project(dbt_dir, "feedback_update")
    written: list[str] = []
    for item in payload.get("files", []):
        path = _safe_dbt_path(dbt_dir, item.get("path", ""))
        content = item.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        _write(path, content, written)

    plan = payload.get("plan")
    if isinstance(plan, dict):
        plan["source"] = "llm_feedback"
        plan["user_feedback"] = feedback
        _write(Path(dbt_dir) / "transformation_plan.json", json.dumps(plan, indent=2), written)

    if not written:
        raise ValueError("The LLM did not return any dbt files to update")

    return {
        "dbt_path": dbt_dir,
        "message": payload.get("message", "Updated dbt transformations."),
        "plan": plan or {},
        "files": written,
        "backup_path": backup_path,
    }
