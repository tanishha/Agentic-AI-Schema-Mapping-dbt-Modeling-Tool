from models import MigrationState


def table_selection_node(state: MigrationState) -> dict:
    """
    Interrupt point after DDL parsing. The UI selects one or more target tables
    before mapping inference runs.
    """
    return {
        "stage": "MAPPING",
    }
