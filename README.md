# LabGuard
[![CI](https://github.com/tsundarren/labguard/actions/workflows/ci.yml/badge.svg)](https://github.com/tsundarren/labguard/actions/workflows/ci.yml)

Ingests synthetic lab messages (FHIR), validates them against a rules engine to catch
requisition-vs-result discrepancies, and keeps an audit trail of every finding.
**All data is synthetic. No real patient data is used.**

Status: Week 1 of 4 (ingestion + synthetic data). Rules engine, worker, and dashboard come next.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

docker compose up -d --build      # Postgres + API on http://localhost:8000
python -m scripts.seed --count 500
pytest -v                         # needs Postgres from the line above
```

API docs: http://localhost:8000/docs

## Optional: realistic patients from Synthea
Synthea needs Java. Download `synthea-with-dependencies.jar` from the Synthea GitHub
releases and run `java -jar synthea-with-dependencies.jar -p 100`. Copy the FHIR bundles from
`output/fhir/` into `data/synthea/`. The seed script then uses those patients' demographics.
(Synthea doesn't produce orders or specimens, so the seed script builds those itself.)
Without Synthea the seed script falls back to built-in fake patients.

## Week 1: Definition of Done
- [x] `docker compose up` brings up DB + API; `/health` returns ok
- [x] POSTing the same message twice stores it once (201 then 200)
- [x] Running the seed script twice adds **zero** new rows the second time
- [x] `pytest` passes locally and in GitHub Actions
- [x] `data/ground_truth.json` lists the injected errors for every message

## Design decisions

**Idempotent ingestion.** Senders retry. `INSERT ... ON CONFLICT DO NOTHING` on a unique
`idempotency_key` lets the database decide the winner, so concurrent retries can't both insert.
Same key + same payload returns the original (200). Same key + different payload is a 409.
No key supplied: the SHA-256 of the canonical payload is used as the key.

**Same content, new key is stored, not rejected.** The duplicate-submission rule (Week 3)
should *detect and report* that, not silently drop it. The seed script injects these on purpose.

**Raw payload stored as JSONB, untouched.** Every finding can trace back to the exact input,
and rules can be re-run when logic changes.

**Append-only audit log.** The app only INSERTs into `audit_log`.

**Minimal validation at the API boundary.** The API checks only "is this a non-empty FHIR
Bundle". Clinical validation belongs to the rules engine, so bad data gets recorded and flagged
rather than bounced.

**Labeled error injection.** Each injected error breaks exactly one thing (and avoids
accidentally triggering other rules) so detection accuracy can be measured cleanly.
Accuracy scoring covers error-severity rules; "abnormal but plausible" results are warnings.
