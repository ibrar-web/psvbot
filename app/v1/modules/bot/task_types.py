from enum import Enum


class TaskType(str, Enum):
    CREATE_ESTIMATE = "create_estimate"
    ESTIMATE_HISTORY_EXPORT = "estimate_history_export"
    INVOICE_HISTORY_LOOKUP = "invoice_history_lookup"
