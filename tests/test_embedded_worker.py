"""The hosted demo runs the worker as a thread that the API wakes up. Needs Postgres."""
import random
import time

from sqlalchemy import text

from app import worker
from app.db import SessionLocal, engine
from app.ingest import ingest_message
from scripts.seed import fake_patients, generate_dataset

PATIENTS = fake_patients(20, random.Random(1))


def pending() -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM raw_messages WHERE status = 'pending'")).scalar_one()


def ingest(items):
    for item in items:
        with SessionLocal() as session:
            ingest_message(session, item.payload, item.key)


def wait_until_idle(timeout=10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pending() == 0:
            return True
        time.sleep(0.1)
    return False


def test_background_worker_processes_backlog_then_wakes_on_notify(clean_db):
    ingest(generate_dataset(3, 0.0, 0.0, 1, PATIENTS))
    thread = worker.start_background()  # embedded poll interval is 15 minutes: only notify() can wake it
    try:
        assert wait_until_idle(), "backlog was not processed on startup"

        ingest(generate_dataset(2, 0.0, 0.0, 2, PATIENTS))
        worker.notify()
        assert wait_until_idle(), "worker did not wake up on notify()"
    finally:
        worker.stop_background(thread)
    assert not thread.is_alive()
