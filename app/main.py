import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .db import Base, engine, get_session
from .ingest import ingest_message
from .models import RawMessage


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Week 1 shortcut. Swap for Alembic migrations once the schema starts changing.
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="LabGuard", version="0.1.0", lifespan=lifespan)


class FhirBundle(BaseModel):
    """Minimal structural check only. Clinical validation is the rules engine's job."""

    model_config = ConfigDict(extra="allow")  # keep every field we don't model
    resourceType: Literal["Bundle"]
    entry: list[dict] = Field(min_length=1)


class MessageOut(BaseModel):
    id: uuid.UUID
    status: str
    idempotency_key: str
    duplicate: bool


@app.get("/health")
def health(session: Session = Depends(get_session)):
    session.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.post("/messages", response_model=MessageOut, status_code=201)
def post_message(
    bundle: FhirBundle,
    response: Response,
    session: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None, max_length=255),
):
    result = ingest_message(session, bundle.model_dump(mode="json"), idempotency_key or None)
    if result.payload_conflict:
        raise HTTPException(409, "Idempotency key was already used with a different payload.")
    if not result.created:
        response.status_code = 200  # replay of an earlier request, nothing new stored
    msg = result.message
    return MessageOut(
        id=msg.id,
        status=msg.status,
        idempotency_key=msg.idempotency_key,
        duplicate=not result.created,
    )


@app.get("/messages/{message_id}")
def get_message(message_id: uuid.UUID, session: Session = Depends(get_session)):
    msg = session.get(RawMessage, message_id)
    if msg is None:
        raise HTTPException(404, "Message not found.")
    return {
        "id": msg.id,
        "status": msg.status,
        "attempts": msg.attempts,
        "received_at": msg.received_at,
        "payload": msg.payload,
    }
