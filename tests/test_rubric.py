import json

import pytest

from code_erosion.rubric import CATEGORIES, make_finding, summarize


def finding(**changes):
    kwargs = dict(category="knowledge_duplication", severity=2, path="src/order.py",
                  start_line=10, end_line=12, evidence="Two separate tax tables",
                  reason="Tax policy may diverge when changed", check="Confirm these use the same tax policy")
    kwargs.update(changes)
    return make_finding(**kwargs)


def test_all_five_books_have_specific_traceable_lenses():
    assert len(CATEGORIES) == 5
    assert {c.book for c in CATEGORIES.values()} == {
        "A Philosophy of Software Design", "Clean Code", "Domain-Driven Design",
        "Working Effectively with Legacy Code", "The Pragmatic Programmer",
    }
    for code in CATEGORIES:
        item = finding(category=code)
        assert item["source"]["anchor"] and item["source"]["url"].startswith("https://")
        assert item["category"] == code
        assert json.loads(json.dumps(item)) == item


def test_explained_score_preserves_evidence_and_no_aggregate_quality_claim():
    item = finding()
    assert item["score"] == 2
    assert item["location"] == {"path": "src/order.py", "start_line": 10, "end_line": 12}
    assert item["reason"] == "Tax policy may diverge when changed"
    assert item["status"] == "reviewer_judgment_unvalidated"
    assert summarize([item, finding(severity=0)])["counts"] == {"knowledge_duplication": {2: 1, 0: 1}}
    assert summarize([item])["aggregate_score"] is None
    assert summarize([])["counts"] == {}


@pytest.mark.parametrize("changes", [
    {"category": None}, {"category": "imaginary"}, {"severity": -1}, {"severity": 5},
    {"severity": True}, {"severity": 2.0}, {"start_line": 0},
    {"end_line": 9}, {"start_line": True}, {"path": "  "},
    {"evidence": ""}, {"reason": "\n"}, {"check": None},
])
def test_bad_or_unexplained_findings_fail(changes):
    with pytest.raises(ValueError):
        finding(**changes)


def test_summary_rejects_unknown_or_unversioned_records():
    for changed in ({"category": "made_up"}, {"category": None}, {"score": True}, {"schema": 2}):
        bad = {**finding(), **changed}
        with pytest.raises(ValueError):
            summarize([bad])


def test_summary_rejects_non_mapping_record():
    with pytest.raises(ValueError):
        summarize([None])
