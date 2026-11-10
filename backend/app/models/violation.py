from sqlalchemy import Column, Integer, String, ForeignKey, Text
from .base import Base

class Violation(Base):
    __tablename__ = "violation"

    id = Column(Integer, primary_key=True, index=True)
    check_id = Column(Integer, ForeignKey("check.id"))
    rule_id = Column(Integer, ForeignKey("rule.id"))
    page_number = Column(Integer, nullable=True)
    selection_path = Column(String(255), nullable=True)
    paragraph_number = Column(Integer, nullable=True)
    quote = Column(Text, nullable=True)
    expected = Column(Text, nullable=True)
    found = Column(Text, nullable=True)
    location = Column(String(255), nullable=True)
    severity = Column(String(50), nullable=True)
    comment = Column(Text, nullable=True)
    status = Column(String(50), default="open")