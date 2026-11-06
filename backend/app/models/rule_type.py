from sqlalchemy import Column, Integer, String, Text
from .base import Base

class RuleType(Base):
    __tablename__ = "rule_type"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    