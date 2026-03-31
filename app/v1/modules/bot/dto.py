from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class RuntimeCredentials(BaseModel):
    printsmith_url: str = Field(..., min_length=1)
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    company: str | None = None


class RunEstimateRequest(BaseModel):
    credentials: RuntimeCredentials
    quote_record: Dict[str, Any] = Field(default_factory=dict)


class JobQueuePayload(BaseModel):
    quotation_id: str
    tenant_id: Optional[str] = None
    created_by: Optional[str] = None
    user_email: str
    account_name: str
    contact_person: str
    contact_email: str
    contact_phone: str
    requirements: Dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    queue_id: Optional[str] = None


class QueueProcessRequest(BaseModel):
    queue: JobQueuePayload
    credentials: Optional[RuntimeCredentials] = None
