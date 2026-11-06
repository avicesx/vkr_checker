from sqlalchemy import Column, Integer, String, ForeignKey, TIMESTAMP
from sqlalchemy.orm import relationship
from .base import Base

class RuleSet(Base):
    __tablename__ = "ruleset"

    id = Column(Integer, primary_key=True, index=True)
    guideline_id = Column(Integer, ForeignKey("guideline.id"))
    parent_ruleset_id = Column(Integer, ForeignKey("ruleset.id"), nullable=True)
    name = Column(String(255), nullable=False)
    version = Column(Integer, nullable=False)
    generation_method = Column(String(50), nullable=True)
    status = Column(String(50), default="draft")
    trust_level = Column(String(50), nullable=True)
    confirmed_at = Column(TIMESTAMP, nullable=True)
    confirmed_by = Column(Integer, ForeignKey("user.id"), nullable=True)

    # Связь на саму себя (предыдущая версия набора)
    parent_ruleset = relationship("RuleSet", remote_side=[id])