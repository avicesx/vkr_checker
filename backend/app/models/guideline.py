from sqlalchemy import Column, Integer, String, Boolean, Text, ForeignKey
from sqlalchemy.orm import relationship
from .base import Base

class Guideline(Base):
    __tablename__ = "guideline"

    id = Column(Integer, primary_key=True, index=True)
    previous_version_id = Column(Integer, ForeignKey("guideline.id"), nullable=True)
    title = Column(String(255), nullable=False)
    year = Column(Integer, nullable=True)
    file_path = Column(String(512), nullable=True)
    source = Column(String(50), nullable=True)
    raw_text = Column(Text, nullable=True)
    text_hash = Column(String(64), unique=True, nullable=True)
    confirmed = Column(Boolean, default=False)
    relation_type = Column(String(50), nullable=True)

    # Связь на саму себя (предыдущая версия)
    previous_version = relationship("Guideline", remote_side=[id])