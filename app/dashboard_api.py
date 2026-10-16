"""Read endpoints (plus one status update) that power the dashboard.

No authentication yet: fine for a local demo of synthetic data, not for anything real.
"""
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .db import get_session
from .models import AuditLog, Finding, Order, Patient, RawMessage, Result
from .security import is_read_only, require_writable

router = APIRouter(prefix="/api", tags=["dashboard"])

FindingStatus = Literal["open", "resolved", "false_positive"]


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    message_id: uuid.UUID
    rule_id: str
    severity: str
    field: str
    description: str
    status: str
    created_at: datetime


class FindingList(BaseModel):
    total: int
    items: list[FindingOut]


class StatusUpdate(BaseModel):
    status: FindingStatus


@router.get("/config")
def config():
    return {"read_only": is_read_only()}


@router.get("/stats")
def stats(session: Session = Depends(get_session)):
    messages = {"pending": 0, "done": 0, "failed": 0}
    for status, n in session.execute(select(RawMessage.status, func.count()).group_by(RawMessage.status)):
        messages[status] = n
    messages["total"] = sum(messages.values())

    rules: dict[str, dict] = {}
    grouped = select(Finding.rule_id, Finding.severity, Finding.status, func.count()).group_by(
        Finding.rule_id, Finding.severity, Finding.status
    )
    for rule_id, severity, status, n in session.execute(grouped):
        row = rules.setdefault(
            rule_id,
            {"rule_id": rule_id, "severity": severity, "open": 0, "resolved": 0, "false_positive": 0, "total": 0},
        )
        row[status] = row.get(status, 0) + n
        row["total"] += n
    return {"messages": messages, "rules": sorted(rules.values(), key=lambda r: (-r["total"], r["rule_id"]))}


@router.get("/findings", response_model=FindingList)
def list_findings(
    status: FindingStatus | None = None,
    rule_id: str | None = Query(default=None, max_length=50),
    severity: Literal["error", "warning"] | None = None,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    conditions = []
    if status:
        conditions.append(Finding.status == status)
    if rule_id:
        conditions.append(Finding.rule_id == rule_id)
    if severity:
        conditions.append(Finding.severity == severity)

    total = session.scalar(select(func.count()).select_from(Finding).where(*conditions))
    items = session.scalars(
        select(Finding).where(*conditions).order_by(Finding.id.desc()).limit(limit).offset(offset)
    ).all()
    return FindingList(total=total, items=[FindingOut.model_validate(f) for f in items])


@router.patch("/findings/{finding_id}", response_model=FindingOut, dependencies=[Depends(require_writable)])
def update_finding(finding_id: int, body: StatusUpdate, session: Session = Depends(get_session)):
    finding = session.get(Finding, finding_id, with_for_update=True)
    if finding is None:
        raise HTTPException(404, "Finding not found.")
    if finding.status != body.status:
        old = finding.status
        finding.status = body.status
        # Findings are never deleted or silently changed: every transition is audited.
        session.add(
            AuditLog(
                actor="dashboard",
                action="finding.status_changed",
                entity_type="finding",
                entity_id=str(finding.id),
                details={"from": old, "to": body.status, "message_id": str(finding.message_id)},
            )
        )
        session.commit()
    return finding


def _row(obj, columns: list[str]) -> dict:
    return {c: getattr(obj, c) for c in columns}


@router.get("/messages/{message_id}")
def message_detail(message_id: uuid.UUID, session: Session = Depends(get_session)):
    msg = session.get(RawMessage, message_id)
    if msg is None:
        raise HTTPException(404, "Message not found.")
    sid = str(message_id)

    patients = session.scalars(select(Patient).where(Patient.message_id == message_id).order_by(Patient.role)).all()
    order = session.scalar(select(Order).where(Order.message_id == message_id))
    result = session.scalar(select(Result).where(Result.message_id == message_id))
    findings = session.scalars(select(Finding).where(Finding.message_id == message_id).order_by(Finding.id)).all()
    audit = session.scalars(
        select(AuditLog)
        .where(
            or_(
                and_(AuditLog.entity_type == "raw_message", AuditLog.entity_id == sid),
                AuditLog.details["message_id"].astext == sid,
            )
        )
        .order_by(AuditLog.occurred_at, AuditLog.id)
    ).all()

    return {
        "message": {
            "id": msg.id, "status": msg.status, "attempts": msg.attempts,
            "received_at": msg.received_at, "idempotency_key": msg.idempotency_key,
        },
        "patients": [_row(p, ["role", "mrn", "family_name", "given_name", "birth_date"]) for p in patients],
        "order": _row(order, ["test_code", "test_display", "requester", "authored_on"]) if order else None,
        "result": _row(result, ["test_code", "test_display", "value", "unit", "issued",
                                "specimen_type", "specimen_identifier", "collected_at"]) if result else None,
        "findings": [FindingOut.model_validate(f) for f in findings],
        "audit": [_row(a, ["id", "occurred_at", "actor", "action", "entity_type", "entity_id", "details"]) for a in audit],
    }
