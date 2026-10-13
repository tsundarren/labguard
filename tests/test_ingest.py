from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

from app.db import SessionLocal, engine
from app.ingest import ingest_message


def count(table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def test_post_stores_message(client, bundle):
    r = client.post("/messages", json=bundle, headers={"Idempotency-Key": "k1"})
    assert r.status_code == 201
    assert r.json()["status"] == "pending"
    assert r.json()["duplicate"] is False
    assert count("raw_messages") == 1


def test_same_key_twice_stores_once(client, bundle):
    first = client.post("/messages", json=bundle, headers={"Idempotency-Key": "k1"})
    second = client.post("/messages", json=bundle, headers={"Idempotency-Key": "k1"})
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["duplicate"] is True
    assert count("raw_messages") == 1


def test_no_header_dedupes_by_content(client, bundle):
    first = client.post("/messages", json=bundle)
    second = client.post("/messages", json=bundle)
    assert first.json()["id"] == second.json()["id"]
    assert count("raw_messages") == 1


def test_same_key_different_payload_is_409(client, bundle):
    client.post("/messages", json=bundle, headers={"Idempotency-Key": "k1"})
    changed = {**bundle, "id": "different"}
    r = client.post("/messages", json=changed, headers={"Idempotency-Key": "k1"})
    assert r.status_code == 409
    assert count("raw_messages") == 1


def test_same_content_new_key_is_stored_for_duplicate_rule(client, bundle):
    client.post("/messages", json=bundle, headers={"Idempotency-Key": "k1"})
    client.post("/messages", json=bundle, headers={"Idempotency-Key": "k2"})
    assert count("raw_messages") == 2


def test_rejects_non_bundle(client):
    assert client.post("/messages", json={"resourceType": "Patient"}).status_code == 422
    assert client.post("/messages", json={"resourceType": "Bundle", "entry": []}).status_code == 422
    assert count("raw_messages") == 0


def test_audit_entry_written_once(client, bundle):
    client.post("/messages", json=bundle, headers={"Idempotency-Key": "k1"})
    client.post("/messages", json=bundle, headers={"Idempotency-Key": "k1"})
    assert count("audit_log") == 1


def test_get_message_roundtrip(client, bundle):
    msg_id = client.post("/messages", json=bundle).json()["id"]
    r = client.get(f"/messages/{msg_id}")
    assert r.status_code == 200
    assert r.json()["payload"]["entry"][0]["resource"]["id"] == "p1"
    assert client.get("/messages/00000000-0000-0000-0000-000000000000").status_code == 404


def test_concurrent_identical_requests_insert_exactly_once(client, bundle):
    def attempt(_):
        with SessionLocal() as s:
            return ingest_message(s, bundle, "race-key").created

    with ThreadPoolExecutor(max_workers=10) as pool:
        created = list(pool.map(attempt, range(10)))
    assert created.count(True) == 1
    assert count("raw_messages") == 1
    assert count("audit_log") == 1
