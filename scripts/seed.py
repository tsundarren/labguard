#!/usr/bin/env python3
"""Generate synthetic lab messages (FHIR Bundles) with injected, *labeled* errors.

Each message is one Bundle containing:
  - Patient (as on the requisition/order)      id: req-<uuid>
  - Patient (as in the lab system / result)    id: lis-<uuid>
  - ServiceRequest (the order)
  - Specimen
  - Observation (the result)

In a clean message the two Patient resources agree. Injected errors break exactly one
thing, and the answer key is written to data/ground_truth.json so Week 3's accuracy
script can compute precision/recall.

Patient demographics come from Synthea output if present (data/synthea/*.json).
Synthea does not emit ServiceRequest/Specimen, so those are built here.
Falls back to a small built-in generator so you can start without Java.

Usage:
  python -m scripts.seed --count 500 --dry-run          # just write the answer key
  python -m scripts.seed --count 500                    # POST to the running API
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.reference import SPECIMEN_NAMES, TESTS

LOINC = "http://loinc.org"
SPECIMEN_SYSTEM = "http://terminology.hl7.org/CodeSystem/v2-0487"
ERROR_TYPES = [
    "identity_mismatch",
    "test_mismatch",
    "specimen_mismatch",
    "missing_field",
    "timestamp_order",
    "implausible_value",
]
DUPLICATE = "duplicate_submission"
LABELED_RULES = set(ERROR_TYPES) | {DUPLICATE}  # rule ids the answer key can score

FIRST = ["Ava", "Liam", "Noah", "Mia", "Ethan", "Zoe", "Lucas", "Nora", "Omar", "Priya", "Mateo", "Hana"]
LAST = ["Carter", "Nguyen", "Patel", "Brooks", "Rivera", "Okafor", "Kim", "Sanders", "Moreno", "Lindqvist"]


# ---------- patients ----------

def _strip_digits(s: str) -> str:
    return re.sub(r"\d+", "", s)  # Synthea appends digits to names, e.g. "Aaron697"


def _mrn(pid: str) -> str:
    return "MRN-" + pid.replace("-", "")[:8].upper()


def fake_patients(n: int, rng: random.Random) -> list[dict]:
    out = []
    for _ in range(n):
        pid = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        out.append(dict(
            id=pid, mrn=_mrn(pid), given=[rng.choice(FIRST)], family=rng.choice(LAST),
            gender=rng.choice(["female", "male"]),
            birthDate=f"{rng.randint(1940, 2015)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
        ))
    return out


def load_synthea_patients(directory: Path) -> list[dict]:
    patients = []
    for path in sorted(directory.glob("*.json")):
        bundle = json.loads(path.read_text())
        for entry in bundle.get("entry", []):
            r = entry.get("resource", {})
            if r.get("resourceType") != "Patient" or not r.get("birthDate"):
                continue
            names = r.get("name") or []
            official = next((n for n in names if n.get("use") == "official"), names[0] if names else None)
            if not official:
                continue
            family = _strip_digits(official.get("family", "")) or "Doe"
            given = [_strip_digits(g) for g in official.get("given", [])] or ["Alex"]
            patients.append(dict(id=r["id"], mrn=_mrn(r["id"]), given=given, family=family,
                                 gender=r.get("gender", "unknown"), birthDate=r["birthDate"]))
    return patients


# ---------- bundle helpers ----------

def resources(bundle: dict, rtype: str) -> list[dict]:
    return [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == rtype]


def one_resource(bundle: dict, rtype: str) -> dict:
    found = resources(bundle, rtype)
    assert len(found) == 1, f"expected one {rtype}"
    return found[0]


def _uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _patient_resource(rid: str, p: dict) -> dict:
    return {
        "resourceType": "Patient", "id": rid,
        "identifier": [{"system": "urn:labguard:mrn", "value": p["mrn"]}],
        "name": [{"family": p["family"], "given": list(p["given"])}],
        "gender": p["gender"], "birthDate": p["birthDate"],
    }


def _set_result(obs: dict, code: str, value: float) -> None:
    t = TESTS[code]
    obs["code"] = {"coding": [{"system": LOINC, "code": code, "display": t["name"]}]}
    obs["valueQuantity"] = {"value": value, "unit": t["unit"]}
    lo, hi = t["ref"]
    obs["referenceRange"] = [{"low": {"value": lo, "unit": t["unit"]}, "high": {"value": hi, "unit": t["unit"]}}]


def _obs_code(obs: dict) -> str:
    return obs["code"]["coding"][0]["code"]


def make_clean_bundle(rng: random.Random, patient: dict) -> dict:
    code = rng.choice(sorted(TESTS))
    t = TESTS[code]
    lo, hi = t["ref"]
    collected = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=rng.randint(0, 60 * 24 * 270))
    issued = collected + timedelta(minutes=rng.randint(30, 600))
    ids = {k: _uuid(rng) for k in ("bundle", "req", "res", "sr", "spec", "obs")}

    order_patient = _patient_resource(f"req-{ids['req']}", patient)
    result_patient = _patient_resource(f"lis-{ids['res']}", patient)
    service_request = {
        "resourceType": "ServiceRequest", "id": ids["sr"], "status": "active", "intent": "order",
        "code": {"coding": [{"system": LOINC, "code": code, "display": t["name"]}]},
        "subject": {"reference": f"Patient/{order_patient['id']}"},
        "authoredOn": (collected - timedelta(hours=1)).isoformat(),
        "requester": {"display": f"Dr. {rng.choice(LAST)}"},
    }
    specimen = {
        "resourceType": "Specimen", "id": ids["spec"],
        "identifier": [{"system": "urn:labguard:specimen-id", "value": f"SP{rng.randint(10**7, 10**8 - 1)}"}],
        "type": {"coding": [{"system": SPECIMEN_SYSTEM, "code": t["specimen"], "display": SPECIMEN_NAMES[t["specimen"]]}]},
        "subject": {"reference": f"Patient/{result_patient['id']}"},
        "collection": {"collectedDateTime": collected.isoformat()},
    }
    observation = {
        "resourceType": "Observation", "id": ids["obs"], "status": "final",
        "subject": {"reference": f"Patient/{result_patient['id']}"},
        "specimen": {"reference": f"Specimen/{ids['spec']}"},
        "basedOn": [{"reference": f"ServiceRequest/{ids['sr']}"}],
        "issued": issued.isoformat(),
    }
    _set_result(observation, code, round(rng.uniform(lo * 0.85, hi * 1.15), 1))

    parts = [order_patient, result_patient, service_request, specimen, observation]
    return {
        "resourceType": "Bundle", "id": ids["bundle"], "type": "collection",
        "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in parts],
    }


# ---------- error injectors (each breaks exactly one thing) ----------

def _inject_identity_mismatch(rng, b):
    pid = one_resource(b, "Observation")["subject"]["reference"].split("/")[1]
    pat = next(p for p in resources(b, "Patient") if p["id"] == pid)
    kind = rng.choice(["dob", "family", "mrn"])
    if kind == "dob":
        y, m, d = pat["birthDate"].split("-")
        pat["birthDate"] = f"{y}-{m}-{int(d) % 28 + 1:02d}"
    elif kind == "family":
        fam = pat["name"][0]["family"]
        pat["name"][0]["family"] = fam[:-1] + ("a" if fam[-1] != "a" else "e")
    else:
        v = pat["identifier"][0]["value"]
        pat["identifier"][0]["value"] = v[:-1] + ("0" if v[-1] != "0" else "1")


def _inject_test_mismatch(rng, b):
    obs = one_resource(b, "Observation")
    current = _obs_code(obs)
    # Same specimen type, so this doesn't also trigger the specimen rule.
    options = [c for c in TESTS if c != current and TESTS[c]["specimen"] == TESTS[current]["specimen"]]
    other = rng.choice(options)
    lo, hi = TESTS[other]["ref"]
    # Realistic value for the *new* test, so this doesn't also trigger the range rule.
    _set_result(obs, other, round(rng.uniform(lo, hi), 1))


def _inject_specimen_mismatch(rng, b):
    coding = one_resource(b, "Specimen")["type"]["coding"][0]
    wrong = "BLD" if coding["code"] == "SER" else "SER"
    coding["code"], coding["display"] = wrong, SPECIMEN_NAMES[wrong]


def _inject_missing_field(rng, b):
    kind = rng.choice(["collection_time", "specimen_id", "requester"])
    if kind == "collection_time":
        del one_resource(b, "Specimen")["collection"]["collectedDateTime"]
    elif kind == "specimen_id":
        del one_resource(b, "Specimen")["identifier"]
    else:
        del one_resource(b, "ServiceRequest")["requester"]


def _inject_timestamp_order(rng, b):
    collected = datetime.fromisoformat(one_resource(b, "Specimen")["collection"]["collectedDateTime"])
    one_resource(b, "Observation")["issued"] = (collected - timedelta(minutes=rng.randint(5, 300))).isoformat()


def _inject_implausible_value(rng, b):
    obs = one_resource(b, "Observation")
    max_plausible = TESTS[_obs_code(obs)]["plausible"][1]
    obs["valueQuantity"]["value"] = round(max_plausible * rng.uniform(1.5, 5), 1)


INJECTORS = {
    "identity_mismatch": _inject_identity_mismatch,
    "test_mismatch": _inject_test_mismatch,
    "specimen_mismatch": _inject_specimen_mismatch,
    "missing_field": _inject_missing_field,
    "timestamp_order": _inject_timestamp_order,
    "implausible_value": _inject_implausible_value,
}


# ---------- dataset ----------

@dataclass
class SeedItem:
    key: str
    payload: dict
    errors: list[str]


def generate_dataset(count: int, error_rate: float, duplicate_rate: float, seed: int, patients: list[dict]) -> list[SeedItem]:
    rng = random.Random(seed)  # deterministic: same seed -> identical dataset and keys
    items: list[SeedItem] = []
    for i in range(count):
        bundle = make_clean_bundle(rng, rng.choice(patients))
        errors: list[str] = []
        if rng.random() < error_rate:
            etype = rng.choice(ERROR_TYPES)
            INJECTORS[etype](rng, bundle)
            errors = [etype]
        items.append(SeedItem(f"seed{seed}-{i:05d}", bundle, errors))
        if rng.random() < duplicate_rate:
            # Same content, NEW idempotency key: slips past ingestion dedupe on purpose.
            items.append(SeedItem(f"seed{seed}-{i:05d}-dup", copy.deepcopy(bundle), errors + [DUPLICATE]))
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=500)
    ap.add_argument("--error-rate", type=float, default=0.2)
    ap.add_argument("--duplicate-rate", type=float, default=0.03)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--synthea-dir", type=Path, default=Path("data/synthea"))
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--out", type=Path, default=Path("data/ground_truth.json"))
    ap.add_argument("--dry-run", action="store_true", help="write the answer key, don't POST")
    args = ap.parse_args()

    patients = load_synthea_patients(args.synthea_dir) if args.synthea_dir.exists() else []
    if patients:
        print(f"Loaded {len(patients)} patients from Synthea output.")
    else:
        print("No Synthea patients found; using built-in fake patients.")
        patients = fake_patients(100, random.Random(args.seed))

    items = generate_dataset(args.count, args.error_rate, args.duplicate_rate, args.seed, patients)
    manifest = []
    counts: dict[str, int] = {}

    if args.dry_run:
        manifest = [dict(idempotency_key=i.key, message_id=None, errors=i.errors) for i in items]
    else:
        import httpx

        with httpx.Client(base_url=args.api, timeout=15) as client:
            for item in items:
                r = client.post("/messages", json=item.payload, headers={"Idempotency-Key": item.key})
                counts[str(r.status_code)] = counts.get(str(r.status_code), 0) + 1
                manifest.append(dict(
                    idempotency_key=item.key,
                    message_id=r.json().get("id") if r.status_code in (200, 201) else None,
                    errors=item.errors,
                ))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dict(seed=args.seed, items=manifest), indent=2))

    injected = sum(1 for m in manifest if m["errors"] and DUPLICATE not in m["errors"])
    dups = sum(1 for m in manifest if DUPLICATE in m["errors"])
    print(f"Generated {len(items)} messages: {injected} with injected errors, {dups} duplicates.")
    if counts:
        print(f"HTTP results (201 = new, 200 = replay): {counts}")
    print(f"Answer key written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
