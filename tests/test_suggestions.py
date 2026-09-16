import json
from pathlib import Path

from code_erosion import suggestions
from code_erosion.cli import main
from code_erosion.core import extract_functions, parse_source, sloc_lines
from code_erosion.languages import PYTHON, TYPESCRIPT


def report():
    return {
        "root": "/repo", "verbosity": 0.1, "erosion": 0.5, "total_mass": 200.0,
        "functions": [
            {"name": "hot", "file": "/repo/a.py", "start_line": 10, "end_line": 60,
             "sloc": 51, "cc": 20, "language": "python",
             "complexity_drivers": [{"kind": "if_statement", "line": 14}]},
            {"name": "small", "file": "/repo/b.py", "start_line": 1, "end_line": 5,
             "sloc": 5, "cc": 2, "language": "python", "complexity_drivers": []},
        ],
    }


def symbols(source: str, language):
    tree = parse_source(source, language)
    return extract_functions(Path("/repo/sample.ts"), tree, sloc_lines(source, tree, language), language)


def test_typescript_driver_golden_lines_and_nested_ownership():
    source = """function outer(x: any) {
  if (x.outer) x++;
  const inner = () => {
    if (x.a && x.b || x.c ?? x.d) return x ? 1 : 2;
  };
  return x;
}
"""
    outer, inner = symbols(source, TYPESCRIPT)
    # Scoring deliberately stays recursive for continuity with the published metric.
    assert outer.cc == 7
    assert inner.cc == 6
    assert outer.complexity_drivers == ({"kind": "if_statement", "line": 2},)
    assert inner.complexity_drivers == (
        {"kind": "if_statement", "line": 4},
        {"kind": "ternary_expression", "line": 4},
        {"kind": "nullish_coalescing", "line": 4},
        {"kind": "logical_or", "line": 4},
        {"kind": "logical_and", "line": 4},
    )


def test_python_driver_golden_lines():
    source = """def choose(xs):
    for x in xs:
        if x and (x > 2 or x < 0):
            return x if x else None
"""
    (choose,) = symbols(source, PYTHON)
    assert choose.complexity_drivers == (
        {"kind": "for_statement", "line": 2},
        {"kind": "if_statement", "line": 3},
        {"kind": "conditional_expression", "line": 4},
        {"kind": "logical_and", "line": 3},
        {"kind": "logical_or", "line": 3},
    )


def test_v1_schema_golden_object():
    payload = suggestions.build_suggestions(report(), top_n=3)
    assert payload == {
        "schema": 1,
        "repo_root": "/repo",
        "metrics": {"verbosity": 0.1, "erosion": 0.5},
        "selection": {"top_n": 3, "threshold": "CC > 10", "count": 1},
        "discipline": suggestions.DISCIPLINE,
        "suggestions": [{
            "rank": 1,
            "function": "hot",
            "path": "a.py",
            "start_line": 10,
            "end_line": 60,
            "anchor": "a.py:10-60",
            "language": "python",
            "sloc": 51,
            "cyclomatic_complexity": 20,
            "erosion_mass": 142.829,
            "share_of_total_mass": 0.7141,
            "complexity_drivers": [{
                "kind": "if_statement", "line": 14,
                "hint": "conditional branches: group related policy behind one named decision",
            }],
            "guidance": suggestions.DISCIPLINE,
            "verification": [
                "Run the existing focused tests before and after the change.",
                "For parser, serializer, evaluator, or other pure logic, add a differential check over representative and fuzz inputs.",
                "Run the full test/typecheck/lint suite required by the repository.",
                "Run code-erosion before and after; treat the score as evidence, not the target.",
            ],
        }],
    }


def test_ranking_top_n_and_negative_normalization():
    raw = report()
    raw["functions"] = [
        {"name": "b", "file": "/repo/z.py", "start_line": 2, "end_line": 3,
         "sloc": 25, "cc": 12, "language": "python", "complexity_drivers": []},
        {"name": "a", "file": "/repo/a.py", "start_line": 1, "end_line": 2,
         "sloc": 100, "cc": 11, "language": "python", "complexity_drivers": []},
    ]
    assert [item["function"] for item in suggestions.build_suggestions(raw, top_n=1)["suggestions"]] == ["a"]
    empty = suggestions.build_suggestions(raw, top_n=-5)
    assert empty["selection"] == {"top_n": 0, "threshold": "CC > 10", "count": 0}


def test_outputs_are_agent_ready_and_markdown_is_inert(tmp_path: Path):
    raw = report()
    raw["functions"][0]["name"] = "`\n@everyone"
    raw["functions"][0]["file"] = "/repo/evil`\n# heading.py"
    payload = suggestions.build_suggestions(raw)
    js, md = tmp_path / "s.json", tmp_path / "instructions.md"
    suggestions.write_outputs(payload, js, md)
    assert json.loads(js.read_text())["schema"] == 1
    rendered = suggestions.render_markdown(payload)
    assert "\n@everyone" not in rendered
    assert "@\u200beveryone" in rendered
    assert "\n# heading" not in rendered
    assert "Discover the repository's documented test" in md.read_text()


def test_cli_writes_outputs_and_preserves_gate_exit_status(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("def f(x):\n    return x\n")
    baseline = tmp_path / "baseline.json"
    assert main([str(tmp_path), "--write-baseline", str(baseline), "--json"]) == 0

    source.write_text("def f(x):\n" + "".join(f"    if x == {i}: x += 1\n" for i in range(12)) + "    return x\n")
    suggestion_json = tmp_path / "suggestions.json"
    instructions = tmp_path / "instructions.md"
    assert main([
        str(tmp_path), "--check-baseline", str(baseline), "--threshold", "0.01",
        "--suggestions-out", str(suggestion_json),
        "--agent-instructions-out", str(instructions),
    ]) == 1
    assert json.loads(suggestion_json.read_text())["suggestions"][0]["function"] == "f"
    assert "## Execution contract" in instructions.read_text()


def test_action_suggestion_artifacts_are_non_blocking():
    action = (Path(__file__).parents[1] / "action.yml").read_text()
    assert 'if ! code-erosion "$SCAN_PATH" --suggestions-out "$suggestions"' in action
    assert "suggestion artifacts failed; the scan and gate are unaffected" in action
    assert 'if [ -f "$suggestions_md" ]; then cat "$suggestions_md" >> "$comment"; fi' in action
