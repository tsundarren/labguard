import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import Base, engine
from app.main import app


@pytest.fixture
def clean_db():
    """Needs a running Postgres (docker compose up -d db). Empties every table before the test."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE raw_messages, audit_log, patients, orders, results, findings RESTART IDENTITY CASCADE"))


@pytest.fixture
def client(clean_db):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def bundle():
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": {"resourceType": "Patient", "id": "p1"}}],
    }
