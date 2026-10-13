"""End-to-end worker tests. These need Postgres (docker compose up -d db)."""
import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

import app.worker as worker
from app.db import SessionLocal, engine
from app.ingest import ingest_message
from app.models import RawMessage
from scripts.seed import DUPLICATE, fake_patients, generate_dataset

PATIENTS = fake_patients(20, random.Random(1))


def ingest(items):
    ids = {}
    for item in items:
        with SessionLocal() as session:
            ids[item.key] = ingest_message(session, item.payload, item.key).message.id
    return ids


def scalar(sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def rows(sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).all()


def item_with(kind):
    return next(i for i in generate_dataset(300, 1.0, 0.0, 3, PATIENTS) if i.errors == [kind])


def test_end_to_end_one_broken_message(clean_db):
    item = item_with("implausible_value")
    msg_id = ingest([item])[item.key]

    assert worker.process_next() is True
    assert worker.process_next() is False  # queue is now empty

    assert scalar("SELECT status FROM raw_messages WHERE id = :i", i=msg_id) == "done"
    assert [r[0] for r in rows("SELECT rule_id FROM findings")] == ["implausible_value"]
    assert scalar("SELECT count(*) FROM patients") == 2
    assert scalar("SELECT count(*) FROM orders") == 1
    assert scalar("SELECT count(*) FROM results") == 1
    assert scalar("SELECT count(*) FROM audit_log WHERE action = 'message.processed'") == 1
    assert scalar("SELECT count(*) FROM audit_log WHERE action = 'finding.created'") == 1


def test_clean_message_produces_no_findings(clean_db):
    ingest(generate_dataset(1, 0.0, 0.0, 1, PATIENTS))
    worker.drain()
    assert scalar("SELECT count(*) FROM findings") == 0
    assert scalar("SELECT count(*) FROM raw_messages WHERE status = 'done'") == 1


def test_worker_findings_match_the_answer_key(clean_db):
    items = generate_dataset(120, 0.4, 0.05, 21, PATIENTS)
    ids = ingest(items)
    assert worker.drain() == len(items)

    found = defaultdict(set)
    for message_id, rule_id in rows("SELECT message_id, rule_id FROM findings"):
        found[message_id].add(rule_id)
    for item in items:
        assert found.get(ids[item.key], set()) == set(item.errors) - {DUPLICATE}, item.key


def test_reprocessing_a_message_creates_no_duplicates(clean_db):
    item = item_with("identity_mismatch")
    msg_id = ingest([item])[item.key]
    worker.drain()
    findings_before = scalar("SELECT count(*) FROM findings")
    created_before = scalar("SELECT count(*) FROM audit_log WHERE action = 'finding.created'")

    with SessionLocal() as session, session.begin():
        assert worker.process_message(session, session.get(RawMessage, msg_id)) == 0  # nothing new

    assert scalar("SELECT count(*) FROM findings") == findings_before
    assert scalar("SELECT count(*) FROM audit_log WHERE action = 'finding.created'") == created_before
    assert scalar("SELECT count(*) FROM patients") == 2  # rebuilt, not doubled
    assert scalar("SELECT count(*) FROM results") == 1


def test_unparseable_message_is_parked_immediately(clean_db, bundle):
    with SessionLocal() as session:
        msg_id = ingest_message(session, bundle, "bad").message.id
    assert worker.process_next() is True
    assert scalar("SELECT status FROM raw_messages WHERE id = :i", i=msg_id) == "failed"
    assert scalar("SELECT attempts FROM raw_messages WHERE id = :i", i=msg_id) == 1
    assert scalar("SELECT count(*) FROM audit_log WHERE action = 'message.failed'") == 1
    assert scalar("SELECT count(*) FROM patients") == 0
    assert worker.process_next() is False


def test_transient_errors_retry_then_park_and_leave_no_partial_data(clean_db, monkeypatch):
    item = generate_dataset(1, 0.0, 0.0, 1, PATIENTS)[0]
    msg_id = ingest([item])[item.key]

    def boom(_):
        raise RuntimeError("simulated rules failure")

    monkeypatch.setattr(worker, "run_rules", boom)  # fails AFTER the normalized rows were written

    for expected_attempts, expected_status in [(1, "pending"), (2, "pending"), (3, "failed")]:
        assert worker.process_next() is True
        assert scalar("SELECT attempts FROM raw_messages WHERE id = :i", i=msg_id) == expected_attempts
        assert scalar("SELECT status FROM raw_messages WHERE id = :i", i=msg_id) == expected_status
        assert scalar("SELECT count(*) FROM patients") == 0  # the failed transaction rolled back fully

    assert worker.process_next() is False
    assert scalar("SELECT count(*) FROM audit_log WHERE action = 'message.retry'") == 2
    assert scalar("SELECT count(*) FROM audit_log WHERE action = 'message.failed'") == 1


def test_a_poison_message_does_not_block_the_queue(clean_db, bundle):
    with SessionLocal() as session:
        ingest_message(session, bundle, "bad")  # unparseable, oldest in the queue
    good = ingest(generate_dataset(5, 0.0, 0.0, 2, PATIENTS))
    worker.drain()
    assert scalar("SELECT count(*) FROM raw_messages WHERE status = 'done'") == len(good)
    assert scalar("SELECT count(*) FROM raw_messages WHERE status = 'failed'") == 1


def test_two_workers_process_each_message_exactly_once(clean_db):
    items = generate_dataset(80, 0.3, 0.0, 5, PATIENTS)
    ingest(items)

    with ThreadPoolExecutor(max_workers=2) as pool:
        handled = list(pool.map(lambda _: worker.drain(), range(2)))

    assert sum(handled) == len(items)
    assert scalar("SELECT count(*) FROM raw_messages WHERE status = 'done'") == len(items)
    assert scalar("SELECT count(*) FROM audit_log WHERE action = 'message.processed'") == len(items)
    assert scalar("SELECT count(DISTINCT entity_id) FROM audit_log WHERE action = 'message.processed'") == len(items)
