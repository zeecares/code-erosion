import json
import subprocess
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
