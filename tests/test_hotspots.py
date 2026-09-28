import json
import subprocess
from pathlib import Path

import pytest

from code_erosion import hotspots, suggestions
from code_erosion.cli import main


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_commits_count_per_path_once_and_reorder_worklist(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Tester")
    git(tmp_path, "config", "user.email", "test@example.com")
    for name in ("quiet.py", "busy.py"):
        (tmp_path / name).write_text("def f(x):\n    return x\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "initial")
    for i in range(2):
        (tmp_path / "busy.py").write_text(f"# edit {i}\ndef f(x):\n    return x\n")
        git(tmp_path, "add", "busy.py")
        git(tmp_path, "commit", "-qm", "change")
    assert hotspots.file_change_counts(tmp_path) == {"quiet.py": 1, "busy.py": 3}
    raw = {"root": str(tmp_path), "verbosity": 0, "erosion": 0.5,
           "total_mass": 500, "functions": [
               {"name": name, "file": str(tmp_path / file), "start_line": 1,
                "end_line": 30, "sloc": sloc, "cc": 12, "language": "python",
                "complexity_drivers": []}
               for name, file, sloc in (("quiet", "quiet.py", 100),
                                        ("busy", "busy.py", 25))]}
    original = suggestions.build_suggestions(raw)
    ranked = suggestions.build_suggestions(raw, change_counts=hotspots.file_change_counts(tmp_path))
    assert [x["function"] for x in original["suggestions"]] == ["quiet", "busy"]
    assert [x["function"] for x in ranked["suggestions"]] == ["busy", "quiet"]
    assert ranked["suggestions"][0]["file_changes_90d"] == 3
    assert ranked["suggestions"][0]["hotspot_priority"] == 180
    assert "file changes (90d) 3" in suggestions.render_markdown(ranked)
    assert "not show this function changed" in suggestions.render_agent_instructions(ranked)


def test_git_missing_or_subdirectory_refused(tmp_path):
    with pytest.raises(hotspots.HotspotHistoryError):
        hotspots.file_change_counts(tmp_path)
    git(tmp_path, "init", "-q")
    (tmp_path / "sub").mkdir()
    with pytest.raises(hotspots.HotspotHistoryError, match="repository root"):
        hotspots.file_change_counts(tmp_path / "sub")


def test_cli_hotspots_opt_in_no_gate_change(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Tester")
    git(tmp_path, "config", "user.email", "test@example.com")
    source = tmp_path / "sample.py"
    source.write_text("def f(x):\n" + "".join(f"    if x == {i}: x += 1\n" for i in range(12)) + "    return x\n")
    git(tmp_path, "add", "sample.py")
    git(tmp_path, "commit", "-qm", "initial")
    first, second = tmp_path / "mass.json", tmp_path / "hotspots.json"
    assert main([str(tmp_path), "--suggestions-out", str(first)]) == 0
    assert main([str(tmp_path), "--suggestions-out", str(second), "--hotspots"]) == 0
    assert "hotspot_priority" not in json.loads(first.read_text())["suggestions"][0]
    assert json.loads(second.read_text())["suggestions"][0]["file_changes_90d"] == 1


def test_shallow_checkout_refused(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.name", "Tester")
    git(source, "config", "user.email", "test@example.com")
    (source / "a.py").write_text("def f(): pass\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "initial")
    dest = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth=1", source.as_uri(), str(dest)], check=True)
    with pytest.raises(hotspots.HotspotHistoryError, match="shallow"):
        hotspots.file_change_counts(dest)
