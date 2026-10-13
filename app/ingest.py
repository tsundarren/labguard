import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import AuditLog, RawMessage


def content_hash(payload: dict) -> str:
    """SHA-256 of canonical JSON, so key order and whitespace don't matter."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class IngestResult:
    message: RawMessage
    created: bool
    payload_conflict: bool  # same idempotency key, different content


def ingest_message(session: Session, payload: dict, idempotency_key: str | None = None) -> IngestResult:
    digest = content_hash(payload)
    # No key supplied? Fall back to the content hash so blind retries still dedupe.
    key = idempotency_key or f"sha256:{digest}"

    # Atomic "insert if absent". The unique constraint decides the winner, so two
    # simultaneous identical requests cannot both insert (no check-then-insert race).
    stmt = (
        insert(RawMessage)
        .values(id=uuid.uuid4(), idempotency_key=key, content_hash=digest, payload=payload)
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(RawMessage.id)
    )
    new_id = session.execute(stmt).scalar_one_or_none()

    if new_id is not None:
        session.add(
            AuditLog(
                actor="api",
                action="message.received",
                entity_type="raw_message",
                entity_id=str(new_id),
                details={"idempotency_key": key, "content_hash": digest},
            )
        )
        session.commit()
        return IngestResult(session.get(RawMessage, new_id), created=True, payload_conflict=False)

    session.rollback()
    existing = session.scalars(select(RawMessage).where(RawMessage.idempotency_key == key)).one()
    return IngestResult(existing, created=False, payload_conflict=existing.content_hash != digest)
