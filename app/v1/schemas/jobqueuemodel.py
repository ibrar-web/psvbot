from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from beanie import Document
from pydantic import Field


class JobQueueStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    failed = "failed"
    complete = "complete"


class JobQueueDocument(Document):
    quotation_id: str
    tenant_id: Optional[str] = None
    created_by: Optional[str] = None
    machine_name: Optional[str] = None
    status: JobQueueStatus = Field(default=JobQueueStatus.pending)
    retry_count: int = 0
    file_name: Optional[str] = None
    file_url: Optional[str] = None
    last_error: Optional[str] = None
    failure_history: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    class Settings:
        name = "job_queue"
