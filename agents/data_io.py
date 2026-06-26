import json
import os
from typing import Any

import pandas as pd


def _flatten_json_payload(payload: Any) -> pd.DataFrame:
    if isinstance(payload, list):
        return pd.json_normalize(payload)
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                return pd.json_normalize(value)
        return pd.json_normalize(payload)
    raise ValueError("JSON source must contain an object or array of objects")


def load_source_dataframe(path: str, nrows: int | None = None) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return pd.read_csv(path, nrows=nrows)
    if ext == ".json":
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                payload = json.load(f)
            df = _flatten_json_payload(payload)
        except json.JSONDecodeError:
            df = pd.read_json(path, orient="records", lines=True, encoding="utf-8-sig")
        return df.head(nrows) if nrows else df
    raise ValueError(f"Unsupported file type: {ext}")


def display_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)
