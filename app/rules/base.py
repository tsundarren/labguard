from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from ..parsing import ParsedMessage


class Severity(str, Enum):
    ERROR = "error"  # almost certainly wrong, needs a human
    WARNING = "warning"  # unusual but may be legitimate (e.g. abnormal result)


@dataclass(frozen=True)
class FindingData:
    rule_id: str
    severity: Severity
    field: str  # which part of the message the finding is about, e.g. "result.value"
    description: str


class Rule(ABC):
    """One rule = one class. A rule receives a parsed message and returns zero or more findings."""

    id: ClassVar[str]
    severity: ClassVar[Severity]

    @abstractmethod
    def check(self, msg: ParsedMessage) -> list[FindingData]: ...

    def finding(self, field: str, description: str) -> FindingData:
        return FindingData(self.id, self.severity, field, description)


_REGISTRY: dict[str, Rule] = {}


def register(cls):
    """Class decorator: adding a rule means writing a class, never editing the engine."""
    rule_id = getattr(cls, "id", None)
    if not rule_id:
        raise ValueError(f"{cls.__name__} must define a non-empty 'id'")
    if rule_id in _REGISTRY:
        raise ValueError(f"duplicate rule id: {rule_id}")
    _REGISTRY[rule_id] = cls()
    return cls


def unregister(rule_id: str) -> None:
    _REGISTRY.pop(rule_id, None)


def all_rules() -> list[Rule]:
    return list(_REGISTRY.values())
