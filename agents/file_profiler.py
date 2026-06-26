import os
from typing import List

import pandas as pd

from agents.data_io import display_value, load_source_dataframe
from models import ColumnProfile, MigrationState

SAMPLE_ROWS = int(os.getenv("PROFILER_SAMPLE_ROWS", "10000"))


def _infer_type(series: pd.Series) -> str:
    dtype = series.dtype
    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_integer_dtype(dtype):
        return "integer"
    if pd.api.types.is_float_dtype(dtype):
        return "float"
    # Try date detection on object columns
    if pd.api.types.is_object_dtype(dtype):
        sample = series.dropna().head(50)
        try:
            pd.to_datetime(sample, infer_datetime_format=True)
            return "date"
        except Exception:
            pass
    return "string"


def profile_file(path: str) -> List[ColumnProfile]:
    df = load_source_dataframe(path, nrows=SAMPLE_ROWS or None)

    filename = os.path.basename(path)
    profiles: List[ColumnProfile] = []
    for col in df.columns:
        series = df[col]
        non_null = series.dropna()
        sample_vals = [display_value(v) for v in non_null.head(5).tolist()]
        distinct_values = series.dropna().map(display_value)
        profiles.append(
            ColumnProfile(
                source_file=filename,
                column_name=str(col),
                inferred_type=_infer_type(series),
                null_pct=float(round(series.isna().mean(), 4)),
                distinct_count=int(distinct_values.nunique()),
                sample_values=sample_vals,
            )
        )
    return profiles


def file_profiler_agent(state: MigrationState) -> dict:
    catalog: List[ColumnProfile] = []
    events = list(state.get("events", []))

    for path in state["source_files"]:
        profiles = profile_file(path)
        catalog.extend(profiles)
        events.append(
            {
                "type": "profiling_progress",
                "payload": {
                    "file": os.path.basename(path),
                    "columns": len(profiles),
                },
            }
        )

    return {
        "intermediate_catalog": catalog,
        "stage": "READY",
        "events": events,
    }
