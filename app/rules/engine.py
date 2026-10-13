from __future__ import annotations

from collections.abc import Iterable

from ..parsing import ParsedMessage
from .base import FindingData, Rule, all_rules


def run_rules(msg: ParsedMessage, rules: Iterable[Rule] | None = None) -> list[FindingData]:
    """Run every registered rule (or an explicit list, handy in tests).

    A rule that raises is a bug, not a finding: the exception propagates so the worker
    retries and eventually parks the message, instead of silently skipping a rule.
    """
    findings: list[FindingData] = []
    for rule in all_rules() if rules is None else rules:
        findings.extend(rule.check(msg))
    return findings
