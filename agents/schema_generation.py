import json
import os
import re
from collections import defaultdict
from typing import Iterable

from llm_client import get_llm_client, get_model_name
from models import ColumnProfile, MigrationState


def _identifier(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
    if not name:
        name = "field"
    if name[0].isdigit():
        name = f"_{name}"
    return name


def _table_name(filename: str) -> str:
    base = os.path.splitext(os.path.basename(filename))[0]
    base = re.sub(r"^source\d*[_-]+", "", base, flags=re.IGNORECASE)
    for prefix in ("source_", "src_", "raw_"):
        if base.lower().startswith(prefix):
            base = base[len(prefix):]
    return _identifier(base)


def _sql_type(profile: ColumnProfile) -> str:
    inferred = profile.get("inferred_type")
    if inferred == "integer":
        return "INTEGER"
    if inferred == "float":
        return "REAL"
    if inferred == "boolean":
        return "INTEGER"
    return "TEXT"


def _strip_markdown(text: str) -> str:
    if "```" not in text:
        return text.strip()
    for part in text.split("```"):
        stripped = part.strip()
        if stripped.lower().startswith("sql"):
            stripped = stripped[3:].strip()
        if "CREATE TABLE" in stripped.upper():
            return stripped
    return text.strip()


def _fallback_schema(catalog: Iterable[ColumnProfile]) -> str:
    by_file: dict[str, list[ColumnProfile]] = defaultdict(list)
    for profile in catalog:
        by_file[profile["source_file"]].append(profile)

    table_pks: dict[str, str] = {}
    for filename, profiles in by_file.items():
        table = _table_name(filename)
        id_candidates = [
            p for p in profiles
            if _identifier(p["column_name"]).endswith("id")
            and p["null_pct"] == 0
            and p["distinct_count"] > 0
        ]
        if id_candidates:
            table_pks[table] = _identifier(id_candidates[0]["column_name"])

    statements: list[str] = []
    for filename, profiles in by_file.items():
        table = _table_name(filename)
        pk_col = table_pks.get(table)
        lines: list[str] = []
        for profile in profiles:
            col = _identifier(profile["column_name"])
            pieces = [f"  {col}", _sql_type(profile)]
            if col == pk_col:
                pieces.append("PRIMARY KEY")
            elif profile["null_pct"] == 0:
                pieces.append("NOT NULL")
            lines.append(" ".join(pieces))

        for profile in profiles:
            col = _identifier(profile["column_name"])
            for ref_table, ref_col in table_pks.items():
                if ref_table != table and col == ref_col:
                    lines.append(f"  FOREIGN KEY ({col}) REFERENCES {ref_table}({ref_col})")
                    break

        statements.append(f"CREATE TABLE {table} (\n" + ",\n".join(lines) + "\n);")
    return "\n\n".join(statements)


def _build_prompt(catalog: list[ColumnProfile]) -> str:
    catalog_json = json.dumps(catalog, indent=2)
    return (
        "Generate a target SQLite schema for a data migration project.\n"
        "Use the source-file profiles below to infer normalized target tables, "
        "primary keys, foreign keys, and practical column types.\n\n"
        "Requirements:\n"
        "- Return SQL DDL only. No prose and no markdown fences.\n"
        "- Use CREATE TABLE statements compatible with SQLite.\n"
        "- Include PRIMARY KEY constraints where source IDs are clear.\n"
        "- If you create a synthetic ID column that does not exist in the source files, "
        "define it as INTEGER PRIMARY KEY AUTOINCREMENT so it can be skipped during mapping.\n"
        "- Include FOREIGN KEY constraints for relationships you can infer from shared IDs.\n"
        "- Prefer table-level FOREIGN KEY (column) REFERENCES parent_table(parent_column) constraints.\n"
        "- Use NOT NULL only when the source profile strongly supports it.\n"
        "- Prefer stable snake_case table and column names.\n\n"
        f"Source catalog:\n{catalog_json}"
    )


def _build_feedback_prompt(
    catalog: list[ColumnProfile],
    current_ddl: str,
    feedback: str,
) -> str:
    catalog_json = json.dumps(catalog, indent=2)
    return (
        "Revise this generated SQLite target schema based on the user's feedback.\n"
        "Use the source-file profiles as the ground truth for available fields.\n\n"
        "Requirements:\n"
        "- Return SQL DDL only. No prose and no markdown fences.\n"
        "- Keep valid SQLite CREATE TABLE statements.\n"
        "- Preserve sensible primary keys, foreign keys, and NOT NULL constraints.\n"
        "- If the user asks for an auto-generated ID, define it as INTEGER PRIMARY KEY AUTOINCREMENT.\n"
        "- If relationships exist, include explicit FOREIGN KEY constraints so the ERD can draw arrows.\n"
        "- Apply the requested change if it is compatible with the source catalog.\n"
        "- Prefer stable snake_case table and column names.\n\n"
        f"Source catalog:\n{catalog_json}\n\n"
        f"Current DDL:\n{current_ddl}\n\n"
        f"User feedback:\n{feedback}"
    )


def refine_schema_with_feedback(
    state: MigrationState,
    current_ddl: str,
    feedback: str,
) -> tuple[str, str]:
    catalog = state.get("intermediate_catalog", [])
    try:
        client = get_llm_client()
        model = get_model_name()
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": _build_feedback_prompt(catalog, current_ddl, feedback)}],
            temperature=0,
        )
        return _strip_markdown(response.choices[0].message.content), "llm"
    except Exception as exc:
        return current_ddl, f"unchanged: {exc}"


def schema_generation_agent(state: MigrationState) -> dict:
    events = list(state.get("events", []))
    catalog = state.get("intermediate_catalog", [])

    try:
        client = get_llm_client()
        model = get_model_name()
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": _build_prompt(catalog)}],
            temperature=0,
        )
        ddl = _strip_markdown(response.choices[0].message.content)
        source = "llm"
    except Exception as exc:
        ddl = _fallback_schema(catalog)
        source = f"fallback: {exc}"

    events.append({
        "type": "schema_generated",
        "payload": {"source": source, "ddl": ddl},
    })
    return {
        "ddl_content": ddl,
        "stage": "READY",
        "events": events,
    }
