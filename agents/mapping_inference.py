import json
import os
import uuid
from typing import List

# from openai import OpenAI

from models import MappingItem, MigrationState
from llm_client import get_llm_client, get_model_name


def _build_prompt(state: MigrationState) -> str:
    selected = set(state.get("selected_tables", []))
    target_tables = [
        table for table in state["target_tables"]
        if not selected or table["name"] in selected
    ]
    tables_json = json.dumps(target_tables, indent=2)
    catalog_json = json.dumps(state["intermediate_catalog"], indent=2)
    return (
        "You are a data migration assistant.\n\n"
        "Target schema — multiple related tables:\n"
        f"{tables_json}\n\n"
        "Available source columns (all files combined):\n"
        f"{catalog_json}\n\n"
        "For EVERY column in EVERY target table, return a mapping entry.\n"
        "Return a JSON array only — no prose, no markdown fences.\n"
        "Each element must have exactly these keys:\n"
        "  target_table, target_column, source_file, source_column, "
        "transformation, confidence, reason\n"
        "Strict rules:\n"
        "- source_file and source_column must exactly match an available source column from the catalog\n"
        "- Do not invent source columns that are not in the catalog\n"
        "- If a target column is an INTEGER PRIMARY KEY created by the target schema and no matching source ID exists, set source_file/source_column to null; SQLite will generate it\n"
        "Rules:\n"
        "- confidence is a float 0.0–1.0\n"
        "- If no source column maps, set source_file and source_column to null, confidence to 0.0\n"
        "- For FK columns (e.g. customer_id in a child table), map them to the same source "
        "as the PK in the referenced table\n"
        "- transformation is null unless a concat or cast is needed"
    )

def _call_llm(prompt: str) -> str:
    client = get_llm_client()
    model = get_model_name()
    print(f"[LLM CALL] provider={os.getenv('LLM_PROVIDER', 'ollama')} model={model} base_url={client.base_url}")
    response = client.chat.completions.create(
        model=get_model_name(),
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return response.choices[0].message.content.strip()


def _parse_response(raw: str) -> List[MappingItem]:
    text = raw
    if "```" in text:
        for part in text.split("```"):
            s = part.strip().lstrip("json").strip()
            if s.startswith("["):
                text = s
                break
    data = json.loads(text)
    mappings = []
    for row in data:
        source_file = row.get("source_file")
        source_column = row.get("source_column")
        transformation = row.get("transformation")
        if isinstance(source_file, str) and source_file.strip().lower() in {"", "null", "none", "skip"}:
            source_file = None
        if isinstance(source_column, str) and source_column.strip().lower() in {"", "null", "none", "skip"}:
            source_column = None
        if isinstance(transformation, str) and transformation.strip().lower() in {"", "null", "none"}:
            transformation = None
        mappings.append(MappingItem(
            id=str(uuid.uuid4()),
            target_table=row["target_table"],
            target_column=row["target_column"],
            source_file=source_file,
            source_column=source_column,
            transformation=transformation,
            confidence=float(row.get("confidence", 0.0)),
            reason=row.get("reason", ""),
        ))
    return mappings


def _sanitize_mappings(mappings: List[MappingItem], state: MigrationState) -> List[MappingItem]:
    available = {
        (profile["source_file"], profile["column_name"])
        for profile in state.get("intermediate_catalog", [])
    }
    for mapping in mappings:
        source_file = mapping.get("source_file")
        source_column = mapping.get("source_column")
        if source_file and source_column and (source_file, source_column) not in available:
            mapping["source_file"] = None
            mapping["source_column"] = None
            mapping["confidence"] = 0.0
            mapping["reason"] = "No matching source column exists in the profiled source files."
    return mappings


def mapping_inference_agent(state: MigrationState) -> dict:
    events = list(state.get("events", []))
    prompt = _build_prompt(state)
    raw = _call_llm(prompt)
    try:
        mappings = _parse_response(raw)
    except (json.JSONDecodeError, KeyError):
        raw = _call_llm(prompt)
        mappings = _parse_response(raw)
    mappings = _sanitize_mappings(mappings, state)

    events.append({
        "type": "mapping_ready",
        "payload": {"mappings": [dict(m) for m in mappings]},
    })
    return {
        "proposed_mappings": mappings,
        "stage": "REVIEWING",
        "events": events,
    }
