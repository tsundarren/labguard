import random
from datetime import datetime

from scripts.seed import (DUPLICATE, ERROR_TYPES, TESTS, fake_patients,
                          generate_dataset, one_resource, resources)


def patients():
    return fake_patients(20, random.Random(1))


def test_dataset_is_deterministic():
    assert generate_dataset(100, 0.3, 0.05, 7, patients()) == generate_dataset(100, 0.3, 0.05, 7, patients())


def test_error_rate_is_close_to_requested():
    items = generate_dataset(1000, 0.2, 0.0, 5, patients())
    rate = sum(1 for i in items if i.errors) / len(items)
    assert 0.15 < rate < 0.25


def test_every_error_type_gets_injected():
    items = generate_dataset(1000, 0.5, 0.0, 5, patients())
    assert {e for i in items for e in i.errors} == set(ERROR_TYPES)


def test_duplicates_share_content_but_not_key():
    items = generate_dataset(300, 0.1, 0.2, 9, patients())
    by_key = {i.key: i for i in items}
    dups = [i for i in items if DUPLICATE in i.errors]
    assert dups
    for d in dups:
        original = by_key[d.key.removesuffix("-dup")]
        assert d.payload == original.payload and d.key != original.key


def test_clean_messages_are_internally_consistent():
    for item in generate_dataset(200, 0.0, 0.0, 3, patients()):
        b = item.payload
        pats = {p["id"]: p for p in resources(b, "Patient")}
        sr, obs, spec = (one_resource(b, t) for t in ("ServiceRequest", "Observation", "Specimen"))
        order_p = pats[sr["subject"]["reference"].split("/")[1]]
        result_p = pats[obs["subject"]["reference"].split("/")[1]]
        for field in ("name", "birthDate", "identifier"):
            assert order_p[field] == result_p[field]
        code = obs["code"]["coding"][0]["code"]
        assert code == sr["code"]["coding"][0]["code"]
        assert spec["type"]["coding"][0]["code"] == TESTS[code]["specimen"]
        assert datetime.fromisoformat(obs["issued"]) > datetime.fromisoformat(spec["collection"]["collectedDateTime"])
        lo, hi = TESTS[code]["plausible"]
        assert lo <= obs["valueQuantity"]["value"] <= hi


def test_injected_errors_actually_break_the_message():
    items = generate_dataset(600, 1.0, 0.0, 11, patients())
    for item in items:
        b, kind = item.payload, item.errors[0]
        sr, obs, spec = (one_resource(b, t) for t in ("ServiceRequest", "Observation", "Specimen"))
        code = obs["code"]["coding"][0]["code"]
        if kind == "implausible_value":
            assert obs["valueQuantity"]["value"] > TESTS[code]["plausible"][1]
        elif kind == "specimen_mismatch":
            assert spec["type"]["coding"][0]["code"] != TESTS[code]["specimen"]
        elif kind == "test_mismatch":
            assert code != sr["code"]["coding"][0]["code"]
        elif kind == "timestamp_order":
            assert datetime.fromisoformat(obs["issued"]) < datetime.fromisoformat(spec["collection"]["collectedDateTime"])
        elif kind == "missing_field":
            assert ("collectedDateTime" not in spec["collection"]) or ("identifier" not in spec) or ("requester" not in sr)
        elif kind == "identity_mismatch":
            pats = {p["id"]: p for p in resources(b, "Patient")}
            a = pats[sr["subject"]["reference"].split("/")[1]]
            c = pats[obs["subject"]["reference"].split("/")[1]]
            assert any(a[f] != c[f] for f in ("name", "birthDate", "identifier"))
