# LabGuard

[![CI](https://github.com/tsundarren/labguard/actions/workflows/ci.yml/badge.svg)](https://github.com/tsundarren/labguard/actions/workflows/ci.yml)

Ingests synthetic lab messages (FHIR), validates them against a rules engine to catch
requisition-vs-result discrepancies, and keeps an audit trail of every finding.

**All data is synthetic. No real patient data is used.** The project is inspired by comparing
requisition forms against electronic records in a clinical lab. It is not production healthcare
software and makes no HIPAA-compliance claims.

**Status:** ingestion API and processing engine are working. Remaining rules, an accuracy report,
and a dashboard are in progress (see [Roadmap](#roadmap)).

## How it works

```
Seed script (synthetic FHIR bundles + labeled errors)
        |
POST /messages (FastAPI) --> raw_messages (status: pending)
        |
Worker (FOR UPDATE SKIP LOCKED) --> parse --> rules engine
        |
patients / orders / results + findings + audit_log (PostgreSQL)
```

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

docker compose up -d --build      # Postgres + API (http://localhost:8000) + worker
python -m scripts.seed --count 500
docker compose logs -f worker     # watch findings being produced
pytest -v                         # needs Postgres from the first command; wipes all tables
```

Requires Python 3.10+ (3.12 recommended) and Docker. API docs: http://localhost:8000/docs

### Optional: realistic patients from Synthea
Synthea needs Java. Download `synthea-with-dependencies.jar` from the Synthea GitHub
releases and run `java -jar synthea-with-dependencies.jar -p 100`. Copy the FHIR bundles from
`output/fhir/` into `data/synthea/`. The seed script then uses those patients' demographics.
Synthea doesn't produce orders or specimens, so the seed script builds those itself.
Without Synthea it falls back to built-in fake patients.

## Project layout

```
app/
  main.py              FastAPI app: POST /messages, GET /messages/{id}, /health
  ingest.py            idempotent insert logic
  models.py            SQLAlchemy tables
  parsing.py           FHIR Bundle -> plain dataclasses (no database)
  reference.py         test catalog: expected specimen, plausible and reference ranges
  worker.py            queue worker
  rules/               rules framework, registry, engine, rule implementations
scripts/seed.py        synthetic data generator with labeled error injection
tests/                 unit tests (no DB) and integration tests (need Postgres)
```

## Ingestion API

`POST /messages` accepts a FHIR `Bundle` containing the order (`ServiceRequest`), the patient as
written on the requisition and as recorded with the result (`Patient`), the `Specimen`, and the
result (`Observation`). Responses: `201` new message, `200` replay of an earlier request, `409` same
idempotency key reused with a different payload, `422` not a valid Bundle.

### Design decisions

**Idempotent ingestion.** Senders retry. `INSERT ... ON CONFLICT DO NOTHING` on a unique
`idempotency_key` lets the database decide the winner, so concurrent retries can't both insert.
Same key + same payload returns the original (200). Same key + different payload is a 409.
With no key supplied, the SHA-256 of the canonical payload is used as the key.

**Same content, new key is stored, not rejected.** The planned duplicate-submission rule should
*detect and report* that, not silently drop it. The seed script injects these on purpose.

**Raw payload stored as JSONB, untouched.** Every finding can trace back to the exact input,
and rules can be re-run when logic changes.

**Append-only audit log.** The app only INSERTs into `audit_log`.

**Minimal validation at the API boundary.** The API checks only "is this a non-empty FHIR
Bundle". Clinical validation belongs to the rules engine, so bad data gets recorded and flagged
rather than bounced.

**Labeled error injection.** Each injected error breaks exactly one thing (and avoids
accidentally triggering other rules) so detection accuracy can be measured cleanly.
Accuracy scoring covers error-severity rules; "abnormal but plausible" results are warnings.

## Processing Engine

The worker claims pending messages, parses them into normalized tables, runs every registered rule,
stores findings, and writes audit entries.

```bash
docker compose up -d --scale worker=2   # run two workers on the same queue
python -m app.worker --drain            # or run one locally until the queue is empty
```

### Rules (ids match the seed generator's answer key)

| Rule id | Severity | Checks |
|---|---|---|
| `identity_mismatch` | error | MRN, DOB, family and given name on the requisition vs. the lab record (one finding per differing field) |
| `test_mismatch` | error | Ordered test code equals performed test code |
| `specimen_mismatch` | error | Specimen type is appropriate for the ordered test |
| `missing_field` | error | Collection time, specimen ID, ordering provider, result value |
| `timestamp_order` | error | Result issued at or after specimen collection |
| `implausible_value` | error | Value inside the physiologically possible range for the test |
| `duplicate_submission` | warning | Identical content already received under a different idempotency key (only the later copy is flagged) |

### Design decisions

**Claim + process in one transaction.** A worker locks a pending message, processes it, and
commits once. Crash or error means rollback and the message is simply `pending` again
(at-least-once processing). There is no committed "processing" state to get stuck in.

**`FOR UPDATE SKIP LOCKED`.** Locked rows are skipped, not waited on. Plain `FOR UPDATE` would make
workers queue behind the same row, and after the wait the row no longer matches
`status = 'pending'`, so a `LIMIT 1` query can return nothing while messages remain.

**Two kinds of failure.** An unparseable message (`ParseError`) can never succeed, so it is parked as
`failed` immediately. An unexpected error (DB hiccup, bug) is retried: the attempt is counted in a *new*
transaction (the old one rolled back) and the message is parked after `WORKER_MAX_ATTEMPTS`. A poison
message therefore cannot block the queue.

**Idempotent processing.** Normalized rows are wiped and rebuilt (delete cascades), and findings have
a unique `(message, rule, field)` constraint with `ON CONFLICT DO NOTHING`. Reprocessing a message,
which happens after a crash or when rules change, can't create duplicates.

**Rules are pure functions over a parsed message.** The parser and rules never touch the database, so
they are unit-tested without Postgres. Adding a rule = one class + `@register`; the engine never changes.

**A buggy rule is not a finding.** If a rule raises, the exception propagates and the message is
retried or parked, instead of silently skipping that rule.

**Findings describe the field, not the patient's values**, so identifiers aren't copied into a second table.

## Verifying it works

**Idempotency.** Run `python -m scripts.seed --count 500` twice. The first run reports `201` for every
message, the second reports `200` for every message, and no new rows are added.

**Detection vs. the answer key.** With a freshly seeded database and the worker finished, the findings
per rule should match the injected errors exactly:

```bash
docker compose exec db psql -U labguard -c "SELECT rule_id, count(*) FROM findings GROUP BY 1 ORDER BY 1;"

python -c "import json,collections;d=json.load(open('data/ground_truth.json'));print(collections.Counter(e for i in d['items'] for e in i['errors'] if e!='duplicate_submission'))"
```

**Concurrency.** The test suite includes a concurrent-insert test for ingestion and a two-worker test
asserting every message is processed exactly once.

## Dashboard

A React + TypeScript (Vite) app for reviewing findings: summary cards, findings by rule, a filterable
findings queue with resolve / false-positive / reopen actions, and a per-message drill-down showing the
requisition vs. lab record side by side with the audit trail.

```bash
docker compose up -d --build        # API + worker + db
python -m scripts.seed --count 500  # something to look at
cd frontend && npm install && npm run dev
```
Open http://localhost:5173. In development, Vite proxies `/api` to the API on port 8000, so there is no CORS setup.

**API:** `GET /api/stats`, `GET /api/findings` (filters: `status`, `rule_id`, `severity`, `limit`, `offset`),
`PATCH /api/findings/{id}`, `GET /api/messages/{id}`. There is no authentication yet. This is a local demo of
synthetic data and would need auth, per-user audit actors, and access controls before anything real.

**Design decisions.** Status changes never delete anything: every transition writes an audit entry with the old
and new state. The dashboard shows field names and descriptions; identifiers appear only on the drill-down page.

## Accuracy report

```bash
python -m scripts.seed --count 500
# wait for the worker to finish, then:
python -m scripts.accuracy              # or --markdown to paste a table here
```

Scores each rule against the seed script's answer key: injected, detected, missed, false positives, precision, recall.

**How to read it.** The injected errors were designed alongside the rules, so near-perfect scores are expected.
This confirms detection works end to end and catches regressions. It is **not** an estimate of accuracy on real data.
Making it more informative means adding harder cases (name typos and nicknames, borderline values, formatting noise)
and measuring what breaks.

| Rule | Injected | Detected | Missed | False pos. | Precision | Recall |
|---|---|---|---|---|---|---|
| identity_mismatch | 16 | 16 | 0 | 0 | 100.0% | 100.0% |
| test_mismatch | 13 | 13 | 0 | 0 | 100.0% | 100.0% |
| specimen_mismatch | 17 | 17 | 0 | 0 | 100.0% | 100.0% |
| missing_field | 14 | 14 | 0 | 0 | 100.0% | 100.0% |
| timestamp_order | 16 | 16 | 0 | 0 | 100.0% | 100.0% |
| implausible_value | 20 | 20 | 0 | 0 | 100.0% | 100.0% |
| duplicate_submission | 10 | 10 | 0 | 0 | 100.0% | 100.0% |
| ALL (micro) | 106 | 106 | 0 | 0 | 100.0% | 100.0% |

510 messages scored

## Roadmap

- [ ] Rule: abnormal-but-plausible results (warning severity)
- [ ] Rule: duplicate submission (same content, different idempotency key)
- [ ] Accuracy report: precision and recall against `data/ground_truth.json`
- [ ] React dashboard: findings queue, error rates by rule, message drill-down
- [ ] Deployment, architecture diagram, demo GIF, load test
- [ ] Stretch: HL7 v2 input, Alembic migrations, retry backoff

## Known limits

- The schema is created with `create_all`; add Alembic before changing existing tables.
- Failed retries have no backoff delay.
- If rule logic changes, stale findings from the old logic are not auto-resolved.
- Reference ranges are a small illustrative catalog, not a clinical lab catalog.
