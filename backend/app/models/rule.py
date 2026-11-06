from sqlalchemy import Column, Integer, String, ForeignKey, Text, Float, JSON
from sqlalchemy.orm import relationship
from .base import Base

class Rule(Base):
    __tablename__ = "rule"

    id = Column(Integer, primary_key=True, index=True)
    ruleset_id = Column(Integer, ForeignKey("ruleset.id"))
    rule_type_id = Column(Integer, ForeignKey("rule_type.id"))
    inherited_from_rule_id = Column(Integer, ForeignKey("rule.id"), nullable=True)
    name = Column(String(255), nullable=False)
    params = Column(JSON, nullable=True)
    scope = Column(String(50), nullable=True)
    quote = Column(Text, nullable=True)
    quote_location = Column(String(255), nullable=True)
    confidence = Column(Float, nullable=True)
    status = Column(String(50), default="draft")
    origin = Column(String(50), nullable=True)
    target_element = Column(String(50), nullable=True)
    verification_method = Column(String(50), nullable=True)
    strictness = Column(String(50), nullable=True)

    # Связь на саму себя (наследование правила)
    inherited_from = relationship("Rule", remote_side=[id])