"""Dashboard API tests. Need Postgres (docker compose up -d db)."""
import random
from collections import Counter

from app import worker
from scripts.seed import fake_patients, generate_dataset

PATIENTS = fake_patients(20, random.Random(1))


def load(client, n=40, seed=4):
    items = generate_dataset(n, 0.5, 0.0, seed, PATIENTS)
    ids = {}
    for item in items:
        r = client.post("/messages", json=item.payload, headers={"Idempotency-Key": item.key})
        assert r.status_code == 201
        ids[item.key] = r.json()["id"]
    worker.drain()
    return items, ids


def test_stats(client):
    items, _ = load(client)
    stats = client.get("/api/stats").json()
    assert stats["messages"]["done"] == len(items) == stats["messages"]["total"]
    got = {r["rule_id"]: r for r in stats["rules"]}
    for rule, n in Counter(e for i in items for e in i.errors).items():
        assert got[rule]["total"] == n
        assert got[rule]["open"] == n and got[rule]["resolved"] == 0


def test_findings_filtering_and_pagination(client):
    load(client)
    open_findings = client.get("/api/findings?status=open&limit=200").json()
    assert open_findings["total"] == len(open_findings["items"]) > 0

    rule = open_findings["items"][0]["rule_id"]
    filtered = client.get(f"/api/findings?rule_id={rule}&limit=200").json()
    assert filtered["items"] and all(f["rule_id"] == rule for f in filtered["items"])

    page = client.get("/api/findings?limit=2&offset=0").json()
    assert len(page["items"]) == 2 and page["total"] >= 2

    assert client.get("/api/findings?status=bogus").status_code == 422
    assert client.get("/api/findings?limit=0").status_code == 422


def test_status_update_is_audited_and_validated(client):
    load(client)
    finding = client.get("/api/findings?limit=1").json()["items"][0]
    url = f"/api/findings/{finding['id']}"

    r = client.patch(url, json={"status": "false_positive"})
    assert r.status_code == 200 and r.json()["status"] == "false_positive"
    client.patch(url, json={"status": "false_positive"})  # same status again: no new audit row

    audit = client.get(f"/api/messages/{finding['message_id']}").json()["audit"]
    changes = [a for a in audit if a["action"] == "finding.status_changed"]
    assert len(changes) == 1 and changes[0]["details"]["from"] == "open" and changes[0]["details"]["to"] == "false_positive"

    assert client.patch(url, json={"status": "nope"}).status_code == 422
    assert client.patch("/api/findings/999999", json={"status": "open"}).status_code == 404


def test_message_detail(client):
    items, ids = load(client)
    broken = next(i for i in items if i.errors)
    detail = client.get(f"/api/messages/{ids[broken.key]}").json()

    assert detail["message"]["status"] == "done"
    assert {p["role"] for p in detail["patients"]} == {"order", "result"}
    assert detail["order"]["test_code"] and detail["result"]["test_code"]
    assert {f["rule_id"] for f in detail["findings"]} >= set(broken.errors)
    assert any(a["action"] == "message.processed" for a in detail["audit"])
    assert client.get("/api/messages/00000000-0000-0000-0000-000000000000").status_code == 404
