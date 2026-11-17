from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class UploadedCheck(Base):
    """Запись загрузки ВКР и методички для API статуса."""

    __tablename__ = "upload_check"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    thesis_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    thesis_path: Mapped[str] = mapped_column(Text, nullable=False)
    guidelines_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    guidelines_path: Mapped[str] = mapped_column(Text, nullable=False)
