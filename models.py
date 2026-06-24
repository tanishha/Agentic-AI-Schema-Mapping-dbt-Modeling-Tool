from typing import TypedDict, Optional, List, Literal


class ColumnProfile(TypedDict):
    source_file: str
    column_name: str
    inferred_type: str  # "string" | "integer" | "float" | "date" | "boolean"
    null_pct: float
    distinct_count: int
    sample_values: List[str]


class TargetColumn(TypedDict):
    name: str
    sql_type: str
    nullable: bool
    primary_key: bool


class ForeignKey(TypedDict):
    column: str
    ref_table: str
    ref_column: str


class TableSchema(TypedDict):
    name: str
    columns: List[TargetColumn]
    foreign_keys: List[ForeignKey]


class MappingItem(TypedDict):
    id: str
    target_table: str
    target_column: str
    source_file: Optional[str]
    source_column: Optional[str]
    transformation: Optional[str]
    confidence: float
    reason: str


class ValidationResult(TypedDict):
    table: str
    row_count: int
    fk_violations: int
    null_violations: int
    status: str  # "ok" | "warning" | "error"


class MigrationState(TypedDict):
    session_id: str
    stage: Literal[
        "UPLOADING", "PROFILING", "READY", "MAPPING",
        "REVIEWING", "MIGRATING", "DONE", "ERROR"
    ]
    source_files: List[str]
    ddl_content: str
    intermediate_catalog: List[ColumnProfile]
    target_tables: List[TableSchema]
    proposed_mappings: List[MappingItem]
    confirmed_mappings: List[MappingItem]
    rows_loaded: dict          # {table_name: {filename: row_count}}
    validation_results: List[ValidationResult]
    error: Optional[str]
    events: List[dict]
