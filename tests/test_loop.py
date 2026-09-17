import json
import subprocess
import os
import sys
from pathlib import Path

from code_erosion.loop import discover_checks, evaluate


def git(root: Path, *args: str):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "src.py").write_text("x = 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_x.py").write_text("def test_x():\n    assert True\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "seed")
    return tmp_path


def test_discovers_python_and_node_checks(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {
        "test": "vitest", "typecheck": "tsc --noEmit", "lint": "eslint ."}}))
    assert discover_checks(tmp_path) == ["npm run test", "npm run typecheck", "npm run lint"]


def test_accepts_improving_patch_with_green_checks(tmp_path):
    root = repo(tmp_path)
    (root / "src.py").write_text("x = 2\n")
    result = evaluate(root, {"erosion": .5, "verbosity": .2},
                      {"erosion": .4, "verbosity": .21}, {"passed": True})
    assert result["accepted"] is True
    assert result["erosion_delta"] == -.1


def test_rejects_baseline_touch_test_weakening_and_no_improvement(tmp_path):
    root = repo(tmp_path)
    (root / ".code-erosion.json").write_text("{}")
    git(root, "add", ".code-erosion.json")
    git(root, "commit", "-m", "baseline")
    (root / ".code-erosion.json").write_text("{\"changed\": true}")
    (root / "tests/test_x.py").write_text("assert True\n")
    result = evaluate(root, {"erosion": .5, "verbosity": .2},
                      {"erosion": .5, "verbosity": .1}, {"passed": True})
    assert result["accepted"] is False
    assert "candidate touched the committed baseline" in result["reasons"]
    assert any("deletes more test lines" in reason for reason in result["reasons"])
    assert "erosion did not improve" in result["reasons"]


def test_workflow_exposes_model_agnostic_executor_contract():
    workflow = Path('.github/workflows/code-erosion-loop.yml').read_text()
    action = Path('loop/action.yml').read_text()
    assert 'CODE_EROSION_EXECUTOR_COMMAND' in workflow
    assert 'CODE_EROSION_PROMPT' in action
    assert 'CODE_EROSION_SUGGESTIONS' in action
    assert 'CODE_EROSION_TARGET' in action
    assert 'anthropics/claude-code-action' not in workflow + action


def test_deterministic_fixture_reaches_draft_pr_disposition(tmp_path):
    from code_erosion.cli import _report_json, scan

    fixture = Path("tests/fixtures/loop-accepted")
    root = repo(tmp_path)
    (root / "src.py").unlink()
    (root / "candidate.py").write_text((fixture / "before.py").read_text())
    (root / "tests/behavior_check.py").write_text((fixture / "behavior_check.py").read_text())
    git(root, "add", ".")
    git(root, "commit", "-m", "complex fixture")
    before = _report_json(scan(root))

    (root / "candidate.py").write_text((fixture / "after.py").read_text())
    check = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"], cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)}, capture_output=True, text=True,
    )
    after = _report_json(scan(root))
    result = evaluate(root, before, after, {
        "passed": check.returncode == 0,
        "results": [{"command": "python -m pytest -q", "exit_code": check.returncode}],
    })
    assert result["accepted"] is True
    assert result["disposition"] == "open_draft_pr"
    assert result["erosion_delta"] < 0

    workflow = Path(".github/workflows/code-erosion-loop.yml").read_text()
    assert "steps.candidate.outputs.disposition == 'open_draft_pr'" in workflow
    assert "draft: true" in workflow


def test_noop_standin_discards():
    root = Path(".")
    result = evaluate(root, {"erosion": .5, "verbosity": .2},
                      {"erosion": .5, "verbosity": .2}, {"passed": True})
    assert result["accepted"] is False
    assert result["disposition"] == "discard"
