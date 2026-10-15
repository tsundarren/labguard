#!/usr/bin/env python3
"""Score the rules against the answer key written by the seed script.

    python -m scripts.seed --count 500      # then wait for the worker to finish
    python -m scripts.accuracy [--markdown]

READ THIS BEFORE QUOTING THE NUMBERS: the injected errors were designed alongside the rules,
so a near-perfect score is expected. This proves detection works end to end and catches
regressions. It is NOT an estimate of real-world accuracy. To make it informative, add harder
cases (typos, nicknames, borderline values) and see what breaks.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from scripts.seed import ERROR_TYPES, LABELED_RULES


@dataclass
class RuleScore:
    rule_id: str
    tp: int = 0  # injected and detected
    fp: int = 0  # flagged but not injected
    fn: int = 0  # injected but missed

    @property
    def injected(self) -> int:
        return self.tp + self.fn

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else None


def score_rules(items: list[dict], found: dict[str, set[str]], rules: list[str]) -> dict[str, RuleScore]:
    """items: answer-key rows {message_id, errors}; found: message_id -> set of rule ids that fired."""
    scores = {r: RuleScore(r) for r in rules}
    for item in items:
        message_id = item.get("message_id")
        if message_id is None:
            continue  # never made it into the database (dry run, or the POST failed)
        fired = found.get(message_id, set())
        for rule_id, score in scores.items():
            expected, hit = rule_id in item["errors"], rule_id in fired
            if expected and hit:
                score.tp += 1
            elif expected:
                score.fn += 1
            elif hit:
                score.fp += 1
    return scores


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def format_table(scores: dict[str, RuleScore], markdown: bool = False) -> str:
    total = RuleScore("ALL (micro)", sum(s.tp for s in scores.values()), sum(s.fp for s in scores.values()), sum(s.fn for s in scores.values()))
    header = ["Rule", "Injected", "Detected", "Missed", "False pos.", "Precision", "Recall"]
    rows = [header] + [
        [s.rule_id, str(s.injected), str(s.tp), str(s.fn), str(s.fp), _pct(s.precision), _pct(s.recall)]
        for s in [*scores.values(), total]
    ]
    if markdown:
        lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(header)]
        lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join(lines)
    widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
    return "\n".join("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)) for r in rows)


def load_db() -> tuple[dict[str, set[str]], dict[str, int], int]:
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import Finding, RawMessage

    found: dict[str, set[str]] = defaultdict(set)
    with SessionLocal() as session:
        for message_id, rule_id in session.execute(select(Finding.message_id, Finding.rule_id)):
            found[str(message_id)].add(rule_id)
        counts = dict(session.execute(select(Finding.rule_id, func.count()).group_by(Finding.rule_id)).all())
        pending = session.scalar(select(func.count()).select_from(RawMessage).where(RawMessage.status == "pending"))
    return found, counts, pending


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--answer-key", type=Path, default=Path("data/ground_truth.json"))
    ap.add_argument("--markdown", action="store_true", help="print a markdown table for the README")
    ap.add_argument("--fail-under", type=float, help="exit 1 if any rule's precision or recall is below this (0-1)")
    args = ap.parse_args()

    items = json.loads(args.answer_key.read_text())["items"]
    found, counts, pending = load_db()
    if pending:
        print(f"WARNING: {pending} messages are still pending. Let the worker finish, or they count as misses.\n")

    scored_rules = [r for r in ERROR_TYPES] + ["duplicate_submission"]
    scores = score_rules(items, found, scored_rules)
    skipped = sum(1 for i in items if i.get("message_id") is None)
    print(format_table(scores, args.markdown))
    print(f"\n{len(items) - skipped} messages scored" + (f", {skipped} skipped (no message_id)" if skipped else ""))

    unscored = {r: n for r, n in counts.items() if r not in LABELED_RULES}
    if unscored:
        print("Not scored (no ground truth for these): " + ", ".join(f"{r}={n}" for r, n in sorted(unscored.items())))

    if args.fail_under is not None:
        values = [v for s in scores.values() for v in (s.precision, s.recall) if v is not None]
        if any(v < args.fail_under for v in values):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
