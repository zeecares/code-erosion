"""Record explained, book-grounded human review findings; not an automatic judge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Category:
    code: str
    question: str
    book: str
    anchor: str
    source_url: str


CATEGORIES = {
    category.code: category for category in (
        Category("shallow_abstraction", "Does the interface hide enough complexity to justify it?",
                 "A Philosophy of Software Design", "Deep Modules; Information Hiding",
                 "https://web.stanford.edu/~ouster/cgi-bin/book.php"),
        Category("mixed_responsibilities", "Does this function mix independent responsibilities?",
                 "Clean Code", "Chapter 3: Functions - Do One Thing; One Level of Abstraction per Function",
                 "https://www.oreilly.com/library/view/clean-code-a/9780136083238/chapter03.xhtml"),
        Category("domain_language_drift", "Does terminology conflict with this bounded context's model?",
                 "Domain-Driven Design", "Ubiquitous Language; Bounded Context",
                 "https://www.domainlanguage.com/ddd/"),
        Category("unprotected_behavior_change", "Was existing behavior changed without characterization evidence?",
                 "Working Effectively with Legacy Code", "Seams; Characterization Tests",
                 "https://www.pearson.com/en-us/subject-catalog/p/working-effectively-with-legacy-code/P200000008984/9780131177055"),
        Category("knowledge_duplication", "Is the same changing knowledge encoded in places that can diverge?",
                 "The Pragmatic Programmer", "The Evils of Duplication (DRY)",
                 "https://pragprog.com/titles/tpp20/the-pragmatic-programmer-20th-anniversary-edition/"),
    )
}


def make_finding(*, category: str, severity: int, path: str, start_line: int,
                 end_line: int, evidence: str, reason: str, check: str) -> dict:
    """Validate an explicitly reviewed observation and add its traceable principle.

    Severity is a reviewer judgment: 0 = non-issue/control, 1 = minor,
    2 = moderate, 3 = serious, 4 = critical. It is not inferred from metrics.
    """
    if not isinstance(category, str) or category not in CATEGORIES:
        raise ValueError(f"unknown rubric category: {category!r}")
    if type(severity) is not int or not 0 <= severity <= 4:
        raise ValueError("severity must be an integer from 0 to 4")
    if type(start_line) is not int or type(end_line) is not int or start_line < 1 or end_line < start_line:
        raise ValueError("line span must use positive integers with end_line >= start_line")
    for label, value in (("path", path), ("evidence", evidence), ("reason", reason), ("check", check)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be nonempty text")
    lens = CATEGORIES[category]
    return {
        "schema": SCHEMA_VERSION,
        "category": lens.code,
        "score": severity,
        "location": {"path": path, "start_line": start_line, "end_line": end_line},
        "evidence": evidence,
        "reason": reason,
        "check": check,
        "source": {"book": lens.book, "anchor": lens.anchor, "url": lens.source_url},
        "status": "reviewer_judgment_unvalidated",
    }


def summarize(findings: Iterable[dict]) -> dict:
    """Counts, not a quality score: severity scales are not calibrated."""
    counts: dict[str, dict[int, int]] = {}
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("finding must be a dictionary")
        category, score = finding.get("category"), finding.get("score")
        if finding.get("schema") != SCHEMA_VERSION or not isinstance(category, str) or category not in CATEGORIES or type(score) is not int or not 0 <= score <= 4:
            raise ValueError("invalid finding schema, category, or score")
        bucket = counts.setdefault(category, {})
        bucket[score] = bucket.get(score, 0) + 1
    return {"schema": SCHEMA_VERSION, "counts": counts, "aggregate_score": None}
