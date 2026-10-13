"""Queue worker: claim a pending message, parse it, run the rules, save the results.

Run:  python -m app.worker           (forever, until SIGINT/SIGTERM)
      python -m app.worker --drain   (process everything pending, then exit)
"""
from __future__ import annotations

import argparse
import logging
import signal
import threading
import uuid
from dataclasses import asdict

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .config import MAX_ATTEMPTS, POLL_INTERVAL
from .db import SessionLocal
from .models import AuditLog, Finding, Order, Patient, RawMessage, Result
from .parsing import ParseError, parse_bundle
from .rules import run_rules

log = logging.getLogger("labguard.worker")
_stop = threading.Event()


def claim_one(session: Session) -> RawMessage | None:
    """Lock the oldest pending message that no other worker has locked.

    FOR UPDATE SKIP LOCKED: rows another worker holds are *skipped*, not waited on.
    Plain FOR UPDATE would make workers queue up behind the same row, and after the wait
    the row no longer matches status='pending', so a LIMIT 1 query could come back empty
    even though more messages remain.
    """
    stmt = (
        select(RawMessage)
        .where(RawMessage.status == "pending")
        .order_by(RawMessage.received_at, RawMessage.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return session.execute(stmt).scalar_one_or_none()


def process_message(session: Session, msg: RawMessage) -> int:
    """Normalize + validate one message. Idempotent: safe to run again on the same message.

    Returns the number of NEW findings created.
    """
    parsed = parse_bundle(msg.payload)  # raises ParseError before anything is written

    # Wipe and rebuild the normalized rows (cascades to orders/results).
    session.execute(delete(Patient).where(Patient.message_id == msg.id))
    order_patient = Patient(message_id=msg.id, role="order", **asdict(parsed.order_patient))
    result_patient = Patient(message_id=msg.id, role="result", **asdict(parsed.result_patient))
    session.add_all([order_patient, result_patient])
    session.flush()  # assigns ids
    session.add(Order(message_id=msg.id, patient_id=order_patient.id, **asdict(parsed.order)))
    session.add(Result(message_id=msg.id, patient_id=result_patient.id, **asdict(parsed.result)))
    session.flush()

    findings = run_rules(parsed)
    created = []
    if findings:
        stmt = (
            insert(Finding)
            .values([
                dict(message_id=msg.id, rule_id=f.rule_id, severity=f.severity.value, field=f.field,
                     description=f.description, status="open")
                for f in findings
            ])
            # Unique (message, rule, field): re-running can't duplicate findings.
            .on_conflict_do_nothing(constraint="uq_findings_message_rule_field")
            .returning(Finding.id, Finding.rule_id)
        )
        created = session.execute(stmt).all()

    for finding_id, rule_id in created:
        session.add(AuditLog(actor="worker", action="finding.created", entity_type="finding",
                             entity_id=str(finding_id), details={"message_id": str(msg.id), "rule_id": rule_id}))
    session.add(AuditLog(actor="worker", action="message.processed", entity_type="raw_message",
                         entity_id=str(msg.id), details={"findings_total": len(findings), "findings_new": len(created)}))
    session.execute(update(RawMessage).where(RawMessage.id == msg.id).values(status="done"))
    return len(created)


def fail_permanently(session: Session, message_id: uuid.UUID, exc: Exception) -> None:
    """Unparseable message: retrying can never help, so park it immediately."""
    session.execute(update(RawMessage).where(RawMessage.id == message_id)
                    .values(attempts=RawMessage.attempts + 1, status="failed"))
    session.add(AuditLog(actor="worker", action="message.failed", entity_type="raw_message",
                         entity_id=str(message_id), details={"reason": "unparseable", "error": str(exc)[:500]}))


def record_failure(message_id: uuid.UUID, exc: Exception) -> None:
    """Unexpected error (DB hiccup, bug). Count the attempt in a NEW transaction, since the
    one that failed was rolled back; park the message once it hits MAX_ATTEMPTS."""
    with SessionLocal() as session, session.begin():
        attempts = session.execute(
            update(RawMessage).where(RawMessage.id == message_id)
            .values(attempts=RawMessage.attempts + 1).returning(RawMessage.attempts)
        ).scalar_one()
        parked = attempts >= MAX_ATTEMPTS
        if parked:
            session.execute(update(RawMessage).where(RawMessage.id == message_id).values(status="failed"))
        session.add(AuditLog(actor="worker", action="message.failed" if parked else "message.retry",
                             entity_type="raw_message", entity_id=str(message_id),
                             details={"reason": "error", "error": repr(exc)[:500], "attempts": attempts}))


def process_next() -> bool:
    """Claim and handle one message. Returns False if nothing was available to claim.

    The claim and the processing share ONE transaction. If the worker crashes or errors,
    everything rolls back and the message is simply pending again (at-least-once processing).
    """
    claimed_id = None
    try:
        with SessionLocal() as session, session.begin():
            msg = claim_one(session)
            if msg is None:
                return False
            claimed_id = msg.id
            try:
                with session.begin_nested():  # savepoint: a ParseError undoes only this message's work
                    process_message(session, msg)
            except ParseError as exc:
                log.warning("message %s is unparseable: %s", claimed_id, exc)
                fail_permanently(session, claimed_id, exc)
            return True
    except Exception as exc:
        if claimed_id is None:
            raise  # couldn't even claim (DB down, tables missing); let the caller back off
        log.exception("message %s failed", claimed_id)
        record_failure(claimed_id, exc)
        return True


def drain() -> int:
    """Process until nothing is claimable. Returns how many messages this call handled."""
    handled = 0
    while process_next():
        handled += 1
    return handled


def run() -> None:
    """Long-running loop. A signal sets the stop flag; the current message always finishes first."""
    while not _stop.is_set():
        try:
            handled = process_next()
        except Exception:
            log.exception("worker could not reach the queue; backing off")
            handled = False
        if not handled:
            _stop.wait(POLL_INTERVAL)
    log.info("worker stopped cleanly")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="LabGuard worker")
    parser.add_argument("--drain", action="store_true", help="process everything pending, then exit")
    args = parser.parse_args()

    if args.drain:
        log.info("drained %d messages", drain())
        return

    signal.signal(signal.SIGINT, lambda *_: _stop.set())
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    log.info("worker started (max attempts %d, poll %.1fs)", MAX_ATTEMPTS, POLL_INTERVAL)
    run()


if __name__ == "__main__":
    main()
