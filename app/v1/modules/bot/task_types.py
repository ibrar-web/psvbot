from enum import Enum


class TaskType(str, Enum):
    CREATE_ESTIMATE = "create_estimate"
    ESTIMATE_HISTORY_EXPORT = "estimate_history_export"
    ESTIMATE_HISTORY_LOOKUP = "estimate_history_lookup"
