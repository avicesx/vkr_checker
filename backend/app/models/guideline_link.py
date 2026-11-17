from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text

from .base import Base


class GuidelineLink(Base):
    __tablename__ = "guideline_link"

    id = Column(Integer, primary_key=True, index=True)
    guideline_id = Column(Integer, ForeignKey("guideline.id"))
    org_unit_id = Column(Integer, ForeignKey("org_unit.id"), nullable=True)
    scope_note = Column(Text, nullable=True)
    direction_code = Column(String(50), nullable=True)
    direction_name = Column(String(255), nullable=True)
    program_code = Column(String(50), nullable=True)
    program_name = Column(String(255), nullable=True)
    is_confirmed = Column(Boolean, default=False)
