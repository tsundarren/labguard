"""Rules 1-6. Rule ids match the error labels in the seed generator's answer key."""
from __future__ import annotations

from ..parsing import ParsedMessage
from ..reference import TESTS
from .base import FindingData, Rule, Severity, register


def _norm(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


@register
class IdentityMismatchRule(Rule):
    """Patient on the requisition must match the patient on the result.

    Findings name the field but deliberately don't copy the values, so we don't
    duplicate patient identifiers into a second table.
    """

    id = "identity_mismatch"
    severity = Severity.ERROR

    def check(self, msg: ParsedMessage) -> list[FindingData]:
        a, b = msg.order_patient, msg.result_patient
        checks = [
            ("patient.mrn", _norm(a.mrn), _norm(b.mrn), "MRN"),
            ("patient.birth_date", a.birth_date or "", b.birth_date or "", "Date of birth"),
            ("patient.family_name", _norm(a.family_name), _norm(b.family_name), "Family name"),
            ("patient.given_name", _norm(a.given_name), _norm(b.given_name), "Given name"),
        ]
        return [
            self.finding(field, f"{label} on the requisition does not match the lab record.")
            for field, x, y, label in checks
            if x != y
        ]


@register
class OrderedVsPerformedRule(Rule):
    """The test that was performed must be the test that was ordered."""

    id = "test_mismatch"
    severity = Severity.ERROR

    def check(self, msg: ParsedMessage) -> list[FindingData]:
        ordered, performed = msg.order.test_code, msg.result.test_code
        if ordered != performed:
            return [self.finding("result.test_code", f"Test {ordered} was ordered but the result is for {performed}.")]
        return []


@register
class SpecimenMismatchRule(Rule):
    """The specimen type must be appropriate for the ordered test."""

    id = "specimen_mismatch"
    severity = Severity.ERROR

    def check(self, msg: ParsedMessage) -> list[FindingData]:
        known = TESTS.get(msg.order.test_code)
        actual = msg.result.specimen_type
        if known is None or not actual:
            return []  # can't judge an unknown test; a missing type is out of scope here
        if actual != known["specimen"]:
            return [self.finding("specimen.type", f"Test {msg.order.test_code} needs specimen {known['specimen']} but received {actual}.")]
        return []


@register
class MissingFieldRule(Rule):
    id = "missing_field"
    severity = Severity.ERROR

    def check(self, msg: ParsedMessage) -> list[FindingData]:
        required = [
            ("specimen.collected_at", msg.result.collected_at, "Specimen collection time"),
            ("specimen.identifier", msg.result.specimen_identifier, "Specimen ID"),
            ("order.requester", msg.order.requester, "Ordering provider"),
            ("result.value", msg.result.value, "Result value"),
        ]
        return [self.finding(field, f"{label} is missing.") for field, value, label in required if value is None or value == ""]


@register
class TimestampOrderRule(Rule):
    """A result can't be issued before its specimen was collected."""

    id = "timestamp_order"
    severity = Severity.ERROR

    def check(self, msg: ParsedMessage) -> list[FindingData]:
        collected, issued = msg.result.collected_at, msg.result.issued
        if collected and issued and issued < collected:
            minutes = int((collected - issued).total_seconds() // 60)
            return [self.finding("result.issued", f"Result was issued {minutes} minutes before the specimen was collected.")]
        return []


@register
class ImplausibleValueRule(Rule):
    """Value outside what is physiologically possible: almost surely a data-entry error."""

    id = "implausible_value"
    severity = Severity.ERROR

    def check(self, msg: ParsedMessage) -> list[FindingData]:
        known = TESTS.get(msg.result.test_code)
        value = msg.result.value
        if known is None or value is None:
            return []
        low, high = known["plausible"]
        if value < low or value > high:
            return [self.finding("result.value", f"Value {value} {msg.result.unit or ''} is outside the plausible range {low} to {high} for {msg.result.test_code}.".replace("  ", " "))]
        return []
