from enum import Enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CheckStatus(str, Enum):
    """состояния проверки, доступные через API статуса"""

    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class CheckResponse(BaseModel):
    """данные проверки и её текущий статус"""

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

    id: UUID = Field(description="Уникальный идентификатор проверки")
    status: CheckStatus = Field(
        description=(
            "Текущее состояние. После загрузки задаётся uploaded; значения queued, "
            "processing, completed и failed предназначены для фоновой обработки."
        )
    )
    created_at: datetime = Field(description="Время создания проверки в UTC")


class ErrorResponse(BaseModel):
    """описание ошибки, возвращаемой эндпоинтом"""

    detail: str = Field(description="Текстовое описание ошибки")
