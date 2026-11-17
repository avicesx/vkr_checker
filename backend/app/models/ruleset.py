from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    TIMESTAMP,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship
from .base import Base

class RuleSet(Base):
    __tablename__ = "ruleset"
    __table_args__ = (
        UniqueConstraint(
            "guideline_id",
            "version",
            name="uq_ruleset_guideline_version",
        ),
        Index(
            "uq_ruleset_active_per_guideline",
            "guideline_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    guideline_id = Column(Integer, ForeignKey("guideline.id"), nullable=False)
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