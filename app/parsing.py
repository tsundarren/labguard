"""FHIR Bundle -> plain dataclasses.

No database and no I/O here, so the parser and the rules built on it are unit-testable
without Postgres. Missing *optional* fields become None (the rules flag them).
Structurally unusable messages raise ParseError.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

MRN_SYSTEM = "urn:labguard:mrn"
MAX_ABS_VALUE = Decimal(10) ** 9  # keeps values storable in NUMERIC(14,4)


class ParseError(ValueError):
    """The message is structurally unusable. Retrying can never fix it."""


@dataclass(frozen=True)
class PatientData:
    mrn: str | None
    family_name: str | None
    given_name: str | None
    birth_date: str | None
    gender: str | None


@dataclass(frozen=True)
class OrderData:
    fhir_id: str | None
    test_code: str | None
    test_display: str | None
    requester: str | None
    authored_on: datetime | None


@dataclass(frozen=True)
class ResultData:
    fhir_id: str | None
    test_code: str | None
    test_display: str | None
    value: Decimal | None
    unit: str | None
    issued: datetime | None
    specimen_type: str | None
    specimen_identifier: str | None
    collected_at: datetime | None


@dataclass(frozen=True)
class ParsedMessage:
    order_patient: PatientData  # patient as written on the requisition
    result_patient: PatientData  # patient as recorded with the result
    order: OrderData
    result: ResultData


def parse_bundle(payload: dict) -> ParsedMessage:
    try:
        return _parse(payload)
    except ParseError:
        raise
    except (AttributeError, TypeError, KeyError, IndexError) as exc:
        # Arbitrary JSON arrived from outside; never let it crash the worker unpredictably.
        raise ParseError(f"malformed bundle: {exc!r}") from exc


def _parse(payload: dict) -> ParsedMessage:
    idx = _index(payload)
    sr = _only(idx, "ServiceRequest")
    obs = _only(idx, "Observation")
    order_patient = _ref(idx, sr, "subject", "Patient")
    result_patient = _ref(idx, obs, "subject", "Patient")
    specimen = _ref(idx, obs, "specimen", "Specimen")

    order_code, order_display = _coding(sr)
    result_code, result_display = _coding(obs)
    vq = obs.get("valueQuantity") or {}
    spec_code, _ = _coding(specimen, "type")
    spec_ids = specimen.get("identifier") or []

    return ParsedMessage(
        order_patient=_patient(order_patient),
        result_patient=_patient(result_patient),
        order=OrderData(
            fhir_id=sr.get("id"),
            test_code=order_code,
            test_display=order_display,
            requester=(sr.get("requester") or {}).get("display"),
            authored_on=_dt(sr.get("authoredOn"), "ServiceRequest.authoredOn"),
        ),
        result=ResultData(
            fhir_id=obs.get("id"),
            test_code=result_code,
            test_display=result_display,
            value=_value(vq.get("value")),
            unit=vq.get("unit"),
            issued=_dt(obs.get("issued"), "Observation.issued"),
            specimen_type=spec_code,
            specimen_identifier=(spec_ids[0].get("value") if spec_ids else None),
            collected_at=_dt((specimen.get("collection") or {}).get("collectedDateTime"), "Specimen.collection.collectedDateTime"),
        ),
    )


def _index(bundle: dict) -> dict[str, dict]:
    idx = {}
    for entry in bundle.get("entry") or []:
        res = entry.get("resource") if isinstance(entry, dict) else None
        if isinstance(res, dict) and "resourceType" in res and "id" in res:
            idx[f"{res['resourceType']}/{res['id']}"] = res
    return idx


def _only(idx: dict, rtype: str) -> dict:
    found = [r for key, r in idx.items() if key.startswith(rtype + "/")]
    if len(found) != 1:
        raise ParseError(f"expected exactly one {rtype}, found {len(found)}")
    return found[0]


def _ref(idx: dict, resource: dict, key: str, rtype: str) -> dict:
    ref = (resource.get(key) or {}).get("reference")
    if not isinstance(ref, str) or not ref.startswith(rtype + "/") or ref not in idx:
        raise ParseError(f"{resource.get('resourceType')}.{key} does not resolve to a {rtype} in the bundle ({ref!r})")
    return idx[ref]


def _coding(resource: dict, key: str = "code") -> tuple[str | None, str | None]:
    codings = (resource.get(key) or {}).get("coding") or []
    first = codings[0] if codings and isinstance(codings[0], dict) else {}
    return first.get("code"), first.get("display")


def _patient(res: dict) -> PatientData:
    names = res.get("name") or [{}]
    name = names[0] if isinstance(names[0], dict) else {}
    ids = [i for i in (res.get("identifier") or []) if isinstance(i, dict)]
    mrn = next((i.get("value") for i in ids if i.get("system") == MRN_SYSTEM), None)
    if mrn is None and ids:
        mrn = ids[0].get("value")
    return PatientData(
        mrn=mrn,
        family_name=name.get("family"),
        given_name=" ".join(name.get("given") or []) or None,
        birth_date=res.get("birthDate"),
        gender=res.get("gender"),
    )


def _dt(value, what: str) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ParseError(f"{what} is not a valid ISO datetime: {value!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # naive -> assume UTC so comparisons never crash


def _value(raw) -> Decimal | None:
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ParseError(f"Observation value is not numeric: {raw!r}") from exc
    if not value.is_finite() or abs(value) >= MAX_ABS_VALUE:
        raise ParseError(f"Observation value is out of range: {raw!r}")
    return value
