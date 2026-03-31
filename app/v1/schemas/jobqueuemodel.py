from datetime import datetime
from typing import Any, Dict, List, Optional

from beanie import Document
from pydantic import Field


class JobQueueDocument(Document):
    quotation_id: str
    record_id: Optional[str] = None
    status: str = "pending"
    is_processing: bool = False
    retry_count: int = 0
    last_error: Optional[str] = None
    failure_history: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    class Settings:
        name = "job_queue"
