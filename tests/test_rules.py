import copy
import dataclasses
import random
from decimal import Decimal

import pytest

from app.parsing import parse_bundle
from app.reference import TESTS
from app.rules import FindingData, Rule, Severity, all_rules, register, run_rules
from app.rules.base import unregister
from scripts.seed import DUPLICATE, ERROR_TYPES, fake_patients, generate_dataset

PATIENTS = fake_patients(20, random.Random(1))


def clean_msg():
    return parse_bundle(generate_dataset(1, 0.0, 0.0, 8, PATIENTS)[0].payload)


def fields(findings):
    return {(f.rule_id, f.field) for f in findings}


# ---------- the important one: rules vs. the seed generator's answer key ----------

def test_rules_find_exactly_the_injected_errors():
    """Every injected error type triggers its own rule and no other; clean messages trigger nothing."""
    items = generate_dataset(1500, 0.4, 0.05, 99, PATIENTS)
    seen = set()
    for item in items:
        expected = set(item.errors) - {DUPLICATE}
        found = {f.rule_id for f in run_rules(parse_bundle(item.payload))}
        assert found == expected, f"{item.key}: expected {expected}, got {found}"
        seen |= found
    assert seen == set(ERROR_TYPES)  # the run actually exercised every rule


def test_no_message_produces_duplicate_rule_field_pairs():
    """The findings table has a unique (message, rule, field) constraint; rules must respect it."""
    for item in generate_dataset(500, 0.6, 0.0, 17, PATIENTS):
        findings = run_rules(parse_bundle(item.payload))
        assert len(fields(findings)) == len(findings)


def test_every_rule_is_registered_with_a_severity():
    assert {r.id for r in all_rules()} >= set(ERROR_TYPES)
    assert all(isinstance(r.severity, Severity) for r in all_rules())


# ---------- individual rules ----------

def test_identity_mismatch_reports_each_differing_field_separately():
    msg = clean_msg()
    bad = dataclasses.replace(msg.result_patient, mrn="MRN-OTHER", birth_date="1900-01-01", family_name="Different")
    found = fields(run_rules(dataclasses.replace(msg, result_patient=bad)))
    assert found == {("identity_mismatch", "patient.mrn"), ("identity_mismatch", "patient.birth_date"), ("identity_mismatch", "patient.family_name")}


def test_identity_ignores_case_and_extra_whitespace_in_names():
    msg = clean_msg()
    shouty = dataclasses.replace(msg.result_patient, family_name=f"  {msg.order_patient.family_name.upper()} ")
    assert run_rules(dataclasses.replace(msg, result_patient=shouty)) == []


def test_identity_findings_do_not_contain_patient_values():
    msg = clean_msg()
    bad = dataclasses.replace(msg.result_patient, mrn="SECRET-123")
    finding = run_rules(dataclasses.replace(msg, result_patient=bad))[0]
    assert "SECRET-123" not in finding.description and msg.order_patient.mrn not in finding.description


def test_specimen_rule_skips_unknown_tests():
    msg = clean_msg()
    order = dataclasses.replace(msg.order, test_code="9999-9")
    result = dataclasses.replace(msg.result, test_code="9999-9", specimen_type="XYZ")
    assert run_rules(dataclasses.replace(msg, order=order, result=result)) == []


def test_missing_field_rule_covers_each_required_field():
    msg = clean_msg()
    result = dataclasses.replace(msg.result, collected_at=None, specimen_identifier="", value=None)
    order = dataclasses.replace(msg.order, requester=None)
    found = fields(run_rules(dataclasses.replace(msg, order=order, result=result)))
    assert found == {("missing_field", "specimen.collected_at"), ("missing_field", "specimen.identifier"),
                     ("missing_field", "order.requester"), ("missing_field", "result.value")}


def test_zero_is_not_a_missing_value():
    msg = clean_msg()
    findings = run_rules(dataclasses.replace(msg, result=dataclasses.replace(msg.result, value=Decimal("0"))))
    assert ("missing_field", "result.value") not in fields(findings)


def test_timestamp_rule_allows_equal_times_and_flags_earlier():
    msg = clean_msg()
    same = dataclasses.replace(msg.result, issued=msg.result.collected_at)
    assert run_rules(dataclasses.replace(msg, result=same)) == []
    earlier = dataclasses.replace(msg.result, issued=msg.result.collected_at.replace(year=2020))
    assert fields(run_rules(dataclasses.replace(msg, result=earlier))) == {("timestamp_order", "result.issued")}


def test_plausible_range_boundaries():
    msg = clean_msg()
    low, high = TESTS[msg.result.test_code]["plausible"]
    for value, flagged in [(Decimal(str(low)), False), (Decimal(str(high)), False),
                           (Decimal(str(high)) + Decimal("0.1"), True), (Decimal(str(low)) - Decimal("0.05"), True)]:
        result = dataclasses.replace(msg.result, value=value)
        found = fields(run_rules(dataclasses.replace(msg, result=result)))
        assert (("implausible_value", "result.value") in found) == flagged, value


# ---------- framework ----------

def test_register_adds_a_rule_and_rejects_duplicate_ids():
    @register
    class AlwaysFires(Rule):
        id = "zz_test_rule"
        severity = Severity.WARNING

        def check(self, msg):
            return [self.finding("x", "fired")]

    try:
        assert "zz_test_rule" in {r.id for r in all_rules()}
        found = run_rules(clean_msg())
        assert FindingData("zz_test_rule", Severity.WARNING, "x", "fired") in found
        with pytest.raises(ValueError):
            register(AlwaysFires)
    finally:
        unregister("zz_test_rule")
    assert "zz_test_rule" not in {r.id for r in all_rules()}


def test_run_rules_accepts_an_explicit_rule_list():
    only = [r for r in all_rules() if r.id == "implausible_value"]
    msg = clean_msg()
    result = dataclasses.replace(msg.result, value=Decimal("999999"), collected_at=None)
    assert {f.rule_id for f in run_rules(dataclasses.replace(msg, result=result), only)} == {"implausible_value"}
