from sqlalchemy import Column, Integer, String, ForeignKey, Text, TIMESTAMP
from datetime import datetime
from .base import Base

class Check(Base):
    __tablename__ = "check"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    ruleset_id = Column(Integer, ForeignKey("ruleset.id"))
    work_type = Column(String(50), nullable=True)
    original_filename = Column(String(255), nullable=True)
    file_path = Column(String(512), nullable=True)
    result_docx_path = Column(String(512), nullable=True)
    result_report_path = Column(String(512), nullable=True)
    error_message = Column(Text, nullable=True)
    status = Column(String(50), default="pending")
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    completed_at = Column(TIMESTAMP, nullable=True)