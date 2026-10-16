"""Public-demo protections. Need Postgres for the paths that reach the database."""
import random

from app import worker
from scripts.seed import fake_patients, generate_dataset

PATIENTS = fake_patients(20, random.Random(1))


def test_healthz_is_always_open(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_post_is_open_when_no_key_is_configured(client, bundle, monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    assert client.post("/messages", json=bundle).status_code == 201


def test_post_requires_the_key_when_configured(client, bundle, monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    assert client.post("/messages", json=bundle).status_code == 401
    assert client.post("/messages", json=bundle, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/messages", json=bundle, headers={"X-API-Key": "s3cret"}).status_code == 201


def test_reads_stay_open_when_a_key_is_configured(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    assert client.get("/api/stats").status_code == 200


def test_read_only_mode_blocks_status_changes(client, monkeypatch):
    item = next(i for i in generate_dataset(50, 1.0, 0.0, 3, PATIENTS) if i.errors)
    client.post("/messages", json=item.payload, headers={"Idempotency-Key": item.key})
    worker.drain()
    finding_id = client.get("/api/findings?limit=1").json()["items"][0]["id"]
    url = f"/api/findings/{finding_id}"

    monkeypatch.setenv("READ_ONLY", "1")
    assert client.get("/api/config").json() == {"read_only": True}
    assert client.patch(url, json={"status": "resolved"}).status_code == 403

    monkeypatch.delenv("READ_ONLY")
    assert client.get("/api/config").json() == {"read_only": False}
    assert client.patch(url, json={"status": "resolved"}).status_code == 200
