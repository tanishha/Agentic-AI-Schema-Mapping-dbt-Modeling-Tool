import os
import sqlite3

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from models import MigrationState
from agents.file_profiler import file_profiler_agent
from agents.ddl_parser import ddl_parser_agent
from agents.mapping_inference import mapping_inference_agent
from agents.human_review import human_review_node
from agents.data_migration import data_migration_agent

CHECKPOINT_DB = os.getenv("CHECKPOINT_DB", "data/checkpoints.db")


def build_graph():
    os.makedirs(os.path.dirname(CHECKPOINT_DB), exist_ok=True)

    builder = StateGraph(MigrationState)

    builder.add_node("file_profiler", file_profiler_agent)
    builder.add_node("ddl_parser", ddl_parser_agent)
    builder.add_node("mapping_inference", mapping_inference_agent)
    builder.add_node("human_review", human_review_node)
    builder.add_node("data_migration", data_migration_agent)

    builder.set_entry_point("file_profiler")
    builder.add_edge("file_profiler", "ddl_parser")
    builder.add_edge("ddl_parser", "mapping_inference")
    builder.add_edge("mapping_inference", "human_review")
    builder.add_edge("human_review", "data_migration")
    builder.add_edge("data_migration", END)

    conn = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["human_review"],
    )


compiled_graph = build_graph()
