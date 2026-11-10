from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from .base import Base

class OrgUnit(Base):
    __tablename__ = "org_unit"

    id = Column(Integer, primary_key=True, index=True)
    parent_id = Column(Integer, ForeignKey("org_unit.id"), nullable=True)
    kind = Column(String(50), nullable=False)
    name = Column(String(255), nullable=False)
    code = Column(String(50), nullable=True)
    profile_name = Column(String(255), nullable=True)

    children = relationship("OrgUnit", backref="parent")