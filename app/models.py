import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer,
                        Numeric, String, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class RawMessage(Base):
    """Every inbound message, stored exactly as received.

    status: pending -> done | failed.  There is no committed 'processing' state: a worker
    holds a row lock while it works, and a crash rolls back to 'pending'.
    attempts counts *failed* processing attempts.
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


# ---------- normalized tables (filled by the worker from each raw message) ----------
# Deleting a message's patients cascades to its order and result, which is what makes
# reprocessing a message safe: wipe and rebuild.


class Patient(Base):
    """Two rows per message: the patient as on the requisition ('order') and as recorded with the result."""

    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("raw_messages.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    mrn: Mapped[str | None] = mapped_column(String(100))
    family_name: Mapped[str | None] = mapped_column(String(200))
    given_name: Mapped[str | None] = mapped_column(String(200))
    birth_date: Mapped[str | None] = mapped_column(String(10))  # kept as sent; FHIR allows partial dates
    gender: Mapped[str | None] = mapped_column(String(20))

    __table_args__ = (
        UniqueConstraint("message_id", "role", name="uq_patients_message_role"),
        CheckConstraint("role IN ('order', 'result')", name="ck_patients_role"),
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("raw_messages.id", ondelete="CASCADE"), nullable=False, unique=True)
    patient_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    fhir_id: Mapped[str | None] = mapped_column(String(100))
    test_code: Mapped[str | None] = mapped_column(String(50))
    test_display: Mapped[str | None] = mapped_column(Text)
    requester: Mapped[str | None] = mapped_column(String(200))
    authored_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Result(Base):
    """The observation plus its specimen details (specimen fields are denormalized here for simplicity)."""

    __tablename__ = "results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("raw_messages.id", ondelete="CASCADE"), nullable=False, unique=True)
    patient_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    fhir_id: Mapped[str | None] = mapped_column(String(100))
    test_code: Mapped[str | None] = mapped_column(String(50))
    test_display: Mapped[str | None] = mapped_column(Text)
    value: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    unit: Mapped[str | None] = mapped_column(String(50))
    issued: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    specimen_type: Mapped[str | None] = mapped_column(String(20))
    specimen_identifier: Mapped[str | None] = mapped_column(String(100))
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("raw_messages.id", ondelete="CASCADE"), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    field: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default="open")  # open | resolved | false_positive
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # Reprocessing a message must never create a second copy of the same finding.
        UniqueConstraint("message_id", "rule_id", "field", name="uq_findings_message_rule_field"),
        Index("ix_findings_status_rule", "status", "rule_id"),
    )
