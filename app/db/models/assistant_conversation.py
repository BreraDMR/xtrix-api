"""Private conversations; request keys and ordinals are enforced by PostgreSQL."""
from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.api.core.clock import utcnow
from app.db.base import Base


class AssistantConversation(Base):
    __tablename__ = 'assistant_conversations'
    __table_args__ = (
        UniqueConstraint('owner_user_id', 'client_request_id', name='uq_assistant_conversation_request'),
        CheckConstraint('next_ordinal >= 1', name='ck_assistant_conversation_ordinal'),
        CheckConstraint('length(title) BETWEEN 1 AND 160', name='ck_assistant_conversation_title'),
        Index('ix_assistant_conversation_owner_scope_updated', 'owner_user_id', 'business_id', 'updated_at', 'id'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    business_id = Column(Integer, ForeignKey('businesses.id', ondelete='CASCADE'), nullable=True)
    client_request_id = Column(UUID(as_uuid=True), nullable=False)
    request_hash = Column(String(64), nullable=False)
    title = Column(String(160), nullable=False)
    next_ordinal = Column(Integer, default=1, server_default='1', nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, nullable=False)
    archived_at = Column(DateTime, nullable=True)


class AssistantMessage(Base):
    __tablename__ = 'assistant_messages'
    __table_args__ = (
        UniqueConstraint('conversation_id', 'ordinal', name='uq_assistant_message_ordinal'),
        UniqueConstraint('conversation_id', 'request_id', 'role', name='uq_assistant_message_request_role'),
        CheckConstraint('ordinal >= 1', name='ck_assistant_message_ordinal'),
        CheckConstraint("role IN ('user', 'assistant')", name='ck_assistant_message_role'),
        CheckConstraint("status IN ('pending', 'succeeded', 'failed', 'expired')", name='ck_assistant_message_status'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey('assistant_conversations.id', ondelete='CASCADE'), nullable=False)
    ordinal = Column(Integer, nullable=False)
    request_id = Column(UUID(as_uuid=True), nullable=False)
    request_hash = Column(String(64), nullable=False)
    role = Column(String(16), nullable=False)
    text = Column(Text, nullable=False)
    status = Column(String(16), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    error_code = Column(String(64), nullable=True)
    error_status = Column(Integer, nullable=True)
    error_payload = Column(JSON(none_as_null=True), nullable=True)
    error_retry_after = Column(Integer, nullable=True)
    lease_until = Column(DateTime, nullable=True)
    plan_id = Column(String(64), unique=True, nullable=True)
    plan_payload = Column(JSON(none_as_null=True), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    execution_started_at = Column(DateTime, nullable=True)
    result_payload = Column(JSON(none_as_null=True), nullable=True)
