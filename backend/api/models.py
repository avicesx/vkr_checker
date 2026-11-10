from enum import Enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CheckStatus(str, Enum):
    """Lifecycle states exposed by the check status API."""

    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class CheckResponse(BaseModel):
    """Public representation of a check and its current status."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "c1be2bc2-3f3a-42b6-b7ca-33bd47011af0",
                    "status": "uploaded",
                    "created_at": "2026-11-06T07:00:00Z",
                }
            ]
        }
    )

    id: UUID = Field(description="Unique identifier of the check")
    status: CheckStatus = Field(
        description=(
            "Current lifecycle state. Uploads start as uploaded; queued, "
            "processing, completed, and failed are reserved for async processing."
        )
    )
    created_at: datetime = Field(description="UTC time when the check was created")


class ErrorResponse(BaseModel):
    """Error response returned by API endpoints."""

    detail: str = Field(description="Human-readable explanation of the error")
