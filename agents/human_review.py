from models import MigrationState


def human_review_node(state: MigrationState) -> dict:
    """
    This node is an interrupt point. LangGraph pauses before executing it.
    When resumed (after POST /sessions/{id}/confirm), confirmed_mappings
    will already be in state. We just advance the stage.
    """
    return {
        "stage": "MIGRATING",
    }
