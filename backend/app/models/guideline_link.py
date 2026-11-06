from sqlalchemy import Column, Integer, Boolean, Text, ForeignKey
from .base import Base

class GuidelineLink(Base):
    __tablename__ = "guideline_link"

    id = Column(Integer, primary_key=True, index=True)
    guideline_id = Column(Integer, ForeignKey("guideline.id"))
    org_unit_id = Column(Integer, ForeignKey("org_unit.id"), nullable=True)
    scope_note = Column(Text, nullable=True)
    is_confirmed = Column(Boolean, default=False)