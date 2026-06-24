import json
import os
import uuid
from typing import List

from openai import OpenAI

from models import MappingItem, MigrationState


def _build_prompt(state: MigrationState) -> str:
    tables_json = json.dumps(state["target_tables"], indent=2)
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
        "Rules:\n"
        "- confidence is a float 0.0–1.0\n"
        "- If no source column maps, set source_file and source_column to null, confidence to 0.0\n"
        "- For FK columns (e.g. customer_id in a child table), map them to the same source "
        "as the PK in the referenced table\n"
        "- transformation is null unless a concat or cast is needed"
    )


def _call_llm(prompt: str) -> str:
    client = OpenAI(
        base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.getenv("LLM_API_KEY", "ollama"),
    )
    response = client.chat.completions.create(
        model=os.getenv("LLM_MODEL", "qwen2.5:7b"),
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
    return [
        MappingItem(
            id=str(uuid.uuid4()),
            target_table=row["target_table"],
            target_column=row["target_column"],
            source_file=row.get("source_file"),
            source_column=row.get("source_column"),
            transformation=row.get("transformation"),
            confidence=float(row.get("confidence", 0.0)),
            reason=row.get("reason", ""),
        )
        for row in data
    ]


def mapping_inference_agent(state: MigrationState) -> dict:
    events = list(state.get("events", []))
    prompt = _build_prompt(state)
    raw = _call_llm(prompt)
    try:
        mappings = _parse_response(raw)
    except (json.JSONDecodeError, KeyError):
        raw = _call_llm(prompt)
        mappings = _parse_response(raw)

    events.append({
        "type": "mapping_ready",
        "payload": {"mappings": [dict(m) for m in mappings]},
    })
    return {
        "proposed_mappings": mappings,
        "stage": "REVIEWING",
        "events": events,
    }
