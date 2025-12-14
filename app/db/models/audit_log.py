"""Audit log model for tracking changes."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, ForeignKey
from sqlalchemy.orm import relationship

from app.db.base import Base


class AuditLog(Base):
    """Audit log for tracking changes to entities."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String, nullable=False, index=True)  # 'client', 'visit', 'service', etc.
    entity_id = Column(Integer, nullable=False, index=True)
    action = Column(String, nullable=False, index=True)  # 'create', 'update', 'delete'
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    user_name = Column(String, nullable=True)  # Store name for historical reference
    changes = Column(JSON, nullable=True)  # Store before/after values
    description = Column(Text, nullable=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = relationship("User", foreign_keys=[user_id])









