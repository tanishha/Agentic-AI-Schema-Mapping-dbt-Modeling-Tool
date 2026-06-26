from models import MigrationState


def schema_review_node(state: MigrationState) -> dict:
    return {
        "ddl_content": state["ddl_content"],
        "stage": "READY",
        "events": state.get("events", []),
    }
