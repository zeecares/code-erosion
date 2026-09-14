"""Unit tests for the baseline snapshot / diff / gate logic.

These run without tree-sitter: they exercise baseline.py over synthetic
``--json`` report dicts.
"""

from __future__ import annotations

import json

import pytest

from code_erosion import baseline


def make_report(
    *,
    root: str = "/checkout/repo",
    verbosity: float = 0.10,
    erosion: float = 0.50,
    files: list[tuple[str, int, float]] | None = None,
    functions: list[tuple[str, str, int, int, int]] | None = None,
) -> dict:
    """files: (rel, sloc, verbosity); functions: (rel, name, line, sloc, cc)."""
    files = files if files is not None else [("a.py", 100, 0.10)]
    functions = (
        functions
        if functions is not None
        else [("a.py", "small", 1, 20, 3), ("b.py", "big", 1, 200, 15)]
    )
    per_file = [
        {
            "file": f"{root}/{rel}",
            "sloc": sloc,
            "clone_lines": 0,
            "ast_lines": 0,
            "wrapper_lines": 0,
            "union_lines": round(verb * sloc),
            "verbosity": verb,
        }
        for rel, sloc, verb in files
    ]
    funcs = [
        {
            "name": name,
            "file": f"{root}/{rel}",
            "start_line": line,
            "end_line": line + sloc,
            "sloc": sloc,
            "cc": cc,
            "language": "python",
        }
        for rel, name, line, sloc, cc in functions
    ]
    return {
        "root": root,
        "total_loc": sum(f["sloc"] for f in per_file),
        "files_scanned": len(per_file),
        "verbosity": verbosity,
        "erosion": erosion,
        "total_functions": len(funcs),
        "high_cc_functions": sum(1 for f in funcs if f["cc"] > 10),
        "per_file": per_file,
        "functions": funcs,
    }


def test_snapshot_relativizes_paths():
    snap = baseline.snapshot_from_report(make_report(root="/home/runner/work/x/x"))
    assert set(snap["files"]) == {"a.py"}
    assert set(snap["functions"]) == {"a.py::small", "b.py::big"}
    assert snap["functions"]["b.py::big"]["high"] is True
    assert snap["functions"]["a.py::small"]["high"] is False
    assert snap["functions"]["b.py::big"]["mass"] == pytest.approx(15 * 200**0.5, abs=0.1)


def test_snapshot_same_name_collision_keeps_heavier():
    report = make_report(functions=[
        ("a.py", "dup", 1, 10, 5),
        ("a.py", "dup", 50, 400, 20),
    ])
    snap = baseline.snapshot_from_report(report)
    assert snap["functions"]["a.py::dup"]["cc"] == 20
    assert snap["collisions"] == ["a.py::dup"]


def test_write_and_load_roundtrip(tmp_path):
    path = tmp_path / ".code-erosion.json"
    baseline.write_baseline(make_report(), path)
    loaded = baseline.load_baseline(path)
    assert loaded["metrics"]["erosion"] == 0.50
    assert json.loads(path.read_text())["schema"] == baseline.BASELINE_SCHEMA


def test_load_rejects_non_baseline(tmp_path):
    path = tmp_path / "junk.json"
    path.write_text('{"hello": "world"}')
    with pytest.raises(ValueError):
        baseline.load_baseline(path)


def test_gate_passes_within_threshold():
    base = baseline.snapshot_from_report(make_report(erosion=0.50))
    result = baseline.diff_against_baseline(base, make_report(erosion=0.505), threshold=0.01)
    assert result.gate_pass
    assert result.erosion_delta == pytest.approx(0.005)


def test_gate_fails_beyond_threshold():
    base = baseline.snapshot_from_report(make_report(erosion=0.50))
    result = baseline.diff_against_baseline(base, make_report(erosion=0.52), threshold=0.01)
    assert not result.gate_pass


def test_improvement_passes_and_shows_negative_delta():
    base = baseline.snapshot_from_report(make_report(erosion=0.50))
    result = baseline.diff_against_baseline(base, make_report(erosion=0.40), threshold=0.01)
    assert result.gate_pass
    assert result.erosion_delta == pytest.approx(-0.10)


def test_new_and_resolved_high_cc():
    base = baseline.snapshot_from_report(make_report())
    current = make_report(functions=[
        ("a.py", "small", 1, 20, 3),
        ("b.py", "big", 1, 200, 8),          # was CC 15, now 8: resolved
        ("c.py", "fresh", 1, 90, 12),        # brand new high-CC
    ])
    result = baseline.diff_against_baseline(base, current, threshold=0.01)
    assert result.new_high_cc == ["c.py::fresh"]
    assert result.resolved_high_cc == ["b.py::big"]


def test_mass_movers_tracked():
    base = baseline.snapshot_from_report(make_report())
    current = make_report(functions=[
        ("a.py", "small", 1, 20, 3),
        ("b.py", "big", 1, 400, 20),  # mass 212 -> 400
    ])
    result = baseline.diff_against_baseline(base, current, threshold=0.01)
    assert result.mass_movers_up[0][0] == "b.py::big"
    assert result.mass_movers_up[0][2] > result.mass_movers_up[0][1]


def test_seed_baseline_without_detail_tolerated():
    seed = {
        "schema": 1,
        "created": "2026-09-13T00:00:00Z",
        "metrics": {
            "verbosity": 0.084, "erosion": 0.683, "total_loc": 5800,
            "files_scanned": 36, "total_functions": 300, "high_cc_functions": 20,
        },
        "files": {},
        "functions": {},
    }
    result = baseline.diff_against_baseline(seed, make_report(erosion=0.70), threshold=0.01)
    assert not result.gate_pass
    assert not result.has_base_detail
    md = baseline.render_markdown(result, baseline_path=".code-erosion.json", mode="gate")
    assert "seed file" in md


def test_markdown_contents():
    base = baseline.snapshot_from_report(make_report(erosion=0.50, verbosity=0.10))
    current = make_report(erosion=0.55, verbosity=0.12, functions=[
        ("a.py", "small", 1, 20, 3),
        ("b.py", "big", 1, 200, 15),
        ("c.py", "fresh", 1, 90, 12),
    ])
    result = baseline.diff_against_baseline(base, current, threshold=0.01)
    md = baseline.render_markdown(result, baseline_path=".code-erosion.json", mode="gate")
    assert md.startswith(baseline.COMMENT_MARKER)
    assert "| erosion | 0.500 | 0.550 | +0.050 |" in md
    assert "**Gate: FAIL**" in md
    assert "c.py::fresh" in md
    assert "refresh the baseline" in md


def test_markdown_pass_copy():
    base = baseline.snapshot_from_report(make_report(erosion=0.50))
    result = baseline.diff_against_baseline(base, make_report(erosion=0.50), threshold=0.01)
    md = baseline.render_markdown(result, baseline_path=".code-erosion.json", mode="informational")
    assert "**Mode: informational**" in md
