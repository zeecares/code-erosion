from pathlib import Path
from code_erosion import suggestions


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


def test_build_suggestions_ranks_and_anchors():
    payload = suggestions.build_suggestions(report(), top_n=3)
    assert payload["suggestions"][0]["anchor"] == "a.py:10-60"
    assert payload["suggestions"][0]["complexity_drivers"][0]["line"] == 14
    assert payload["selection"]["count"] == 1


def test_outputs_are_agent_ready(tmp_path: Path):
    payload = suggestions.build_suggestions(report())
    js, md = tmp_path / "s.json", tmp_path / "instructions.md"
    suggestions.write_outputs(payload, js, md)
    assert '"function": "hot"' in js.read_text()
    assert "Open a PR" in md.read_text()
    assert "Fix the code, not the score" in suggestions.render_markdown(payload)
