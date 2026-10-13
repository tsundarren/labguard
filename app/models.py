import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class RawMessage(Base):
    """Every inbound message, stored exactly as received.

    status lifecycle (workers arrive in Week 2): pending -> processing -> done | failed
    """

    __tablename__ = "raw_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Unique: this constraint is what makes ingestion idempotent.
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # NOT unique: same content under a different key is stored, then flagged by the
    # duplicate-submission rule (Week 3).
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Worker query: "oldest pending messages" -> needs this composite index.
    __table_args__ = (Index("ix_raw_messages_status_received", "status", "received_at"),)


class AuditLog(Base):
    """Append-only: the app only ever INSERTs here, never UPDATEs or DELETEs."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
