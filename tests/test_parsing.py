import copy
import random
from datetime import datetime
from decimal import Decimal

import pytest

from app.parsing import ParseError, parse_bundle
from scripts.seed import fake_patients, generate_dataset, one_resource


def clean_bundle():
    patients = fake_patients(10, random.Random(1))
    return generate_dataset(1, 0.0, 0.0, 4, patients)[0].payload


def test_parses_a_clean_message():
    msg = parse_bundle(clean_bundle())
    assert msg.order_patient == msg.result_patient  # clean data: requisition matches lab record
    assert msg.order.test_code == msg.result.test_code
    assert isinstance(msg.result.value, Decimal)
    assert msg.result.collected_at.tzinfo is not None
    assert msg.result.issued > msg.result.collected_at
    assert msg.order.requester and msg.result.specimen_identifier and msg.result.specimen_type


def test_missing_optional_fields_become_none_not_errors():
    b = copy.deepcopy(clean_bundle())
    del one_resource(b, "ServiceRequest")["requester"]
    del one_resource(b, "Specimen")["identifier"]
    del one_resource(b, "Specimen")["collection"]["collectedDateTime"]
    del one_resource(b, "Observation")["valueQuantity"]
    msg = parse_bundle(b)
    assert msg.order.requester is None
    assert msg.result.specimen_identifier is None
    assert msg.result.collected_at is None
    assert msg.result.value is None


def test_naive_datetimes_are_treated_as_utc():
    b = copy.deepcopy(clean_bundle())
    one_resource(b, "Observation")["issued"] = "2026-03-01T10:00:00"
    assert parse_bundle(b).result.issued.tzinfo is not None


def expect_parse_error(bundle):
    with pytest.raises(ParseError):
        parse_bundle(bundle)


def test_structurally_broken_messages_raise_parse_error():
    b = clean_bundle()

    no_obs = copy.deepcopy(b)
    no_obs["entry"] = [e for e in no_obs["entry"] if e["resource"]["resourceType"] != "Observation"]
    expect_parse_error(no_obs)

    bad_ref = copy.deepcopy(b)
    one_resource(bad_ref, "Observation")["subject"]["reference"] = "Patient/does-not-exist"
    expect_parse_error(bad_ref)

    wrong_type_ref = copy.deepcopy(b)
    one_resource(wrong_type_ref, "Observation")["specimen"]["reference"] = one_resource(b, "Observation")["subject"]["reference"]
    expect_parse_error(wrong_type_ref)

    bad_date = copy.deepcopy(b)
    one_resource(bad_date, "Observation")["issued"] = "last tuesday"
    expect_parse_error(bad_date)

    bad_value = copy.deepcopy(b)
    one_resource(bad_value, "Observation")["valueQuantity"]["value"] = "abc"
    expect_parse_error(bad_value)

    nan_value = copy.deepcopy(b)
    one_resource(nan_value, "Observation")["valueQuantity"]["value"] = "NaN"
    expect_parse_error(nan_value)

    huge_value = copy.deepcopy(b)
    one_resource(huge_value, "Observation")["valueQuantity"]["value"] = 1e30
    expect_parse_error(huge_value)

    expect_parse_error({"resourceType": "Bundle", "entry": [{"nonsense": True}]})
    expect_parse_error({"resourceType": "Bundle", "entry": ["not-a-dict"]})


def test_odd_but_valid_shapes_never_crash_with_other_exceptions():
    b = copy.deepcopy(clean_bundle())
    one_resource(b, "Observation")["code"] = {"coding": []}
    msg = parse_bundle(b)  # missing code is tolerated; rules deal with it
    assert msg.result.test_code is None
    assert isinstance(msg.result.collected_at, datetime)
