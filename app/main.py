import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import worker
from .dashboard_api import router as dashboard_router
from .db import Base, engine, get_session
from .ingest import ingest_message
from .models import RawMessage
from .security import require_api_key


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Week 1 shortcut. Swap for Alembic migrations once the schema starts changing.
    Base.metadata.create_all(engine)
    # Hosted demo: run the worker inside the API process (free hosts have no free background workers).
    thread = worker.start_background() if os.getenv("RUN_EMBEDDED_WORKER") == "1" else None
    yield
    if thread is not None:
        worker.stop_background(thread)


app = FastAPI(title="LabGuard", version="0.1.0", lifespan=lifespan)
app.include_router(dashboard_router)


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


@app.get("/healthz")
def healthz():
    """Liveness only. Deliberately does NOT touch the database, so platform health checks
    can't keep a scale-to-zero database awake."""
    return {"status": "ok"}


@app.get("/health")
def health(session: Session = Depends(get_session)):
    session.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.post("/messages", response_model=MessageOut, status_code=201, dependencies=[Depends(require_api_key)])
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
    else:
        worker.notify()  # wake an embedded worker, if there is one; harmless otherwise
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


# Must stay LAST: the production image serves the built dashboard from "/" and this catches
# any path the routes above didn't match.
_static = Path(os.getenv("STATIC_DIR", "static"))
if _static.is_dir():
    app.mount("/", StaticFiles(directory=_static, html=True), name="frontend")
