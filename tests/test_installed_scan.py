"""ZEE-65: an installed wheel must load its packaged rules, and fail loudly without them.

Fast unit-level pins run everywhere; the venv integration test builds the
wheel, installs it non-editably, and proves an installed scan really flags
ast-grep LOC for both languages (installed scans silently resolved zero rule
files before this fix).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from code_erosion import cli
from code_erosion.languages import LANGUAGES

REPO = Path(__file__).resolve().parents[1]

PY_FIXTURE = "def f(x):\n    if x == True:\n        return 1\n    return 0\n"
TS_FIXTURE = (
    "export function g(x: boolean) {\n"
    "  if (x === true) {\n"
    "    return 1;\n"
    "  }\n"
    "  return 0;\n"
    "}\n"
)


# ---------------------------------------------------------- fast unit pins


def test_packaged_rules_resolve_for_every_scanner_language() -> None:
    """Every language key the scanner dispatches on has a real rule set."""
    for language in LANGUAGES:
        texts = cli._rule_texts(language)
        assert texts, language
        assert all("rule:" in text for text in texts), language


def test_missing_rule_tree_raises_instead_of_degrading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "resource_files", lambda _pkg: tmp_path / "nope")
    with pytest.raises(cli.RulesUnavailableError, match="missing"):
        cli.run_ast_grep([tmp_path / "a.py"], "python", [])


def test_empty_rule_tree_raises_instead_of_degrading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "rules" / "python").mkdir(parents=True)
    monkeypatch.setattr(cli, "resource_files", lambda _pkg: tmp_path)
    with pytest.raises(cli.RulesUnavailableError, match="empty"):
        cli.run_ast_grep([tmp_path / "a.py"], "python", [])


def test_missing_ast_grep_binary_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    with pytest.raises(cli.RulesUnavailableError, match="binary"):
        cli.run_ast_grep([tmp_path / "a.py"], "python", [])


def test_foreign_binary_shadowing_sg_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The Unix setgroups `sg` is not ast-grep; it must not run our rules.
    monkeypatch.setattr(cli.shutil, "which", lambda _name: sys.executable)
    with pytest.raises(cli.RulesUnavailableError, match="not ast-grep"):
        cli.run_ast_grep([tmp_path / "a.py"], "python", [])


# ------------------------------------------------- built-wheel integration


def _run_cli(
    venv_python: Path, target: Path, cwd: Path, **kwargs
) -> subprocess.CompletedProcess:
    # cwd must be neutral: `python -m` prepends cwd to sys.path, so running
    # from the repo checkout would import the source tree instead of the
    # installed wheel - and the rule-deletion below would gut the checkout.
    return subprocess.run(
        [str(venv_python), "-m", "code_erosion", str(target), "--json"],
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
        **kwargs,
    )


def test_installed_scan_flags_rules_and_fails_loudly_without_them(
    tmp_path: Path,
) -> None:
    from test_packaging import build_wheel

    wheel = build_wheel(tmp_path / "wheel")
    venv_dir = tmp_path / "venv"
    venv.create(venv_dir, with_pip=True)
    venv_python = venv_dir / "bin" / "python"
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--quiet", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )

    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "a.py").write_text(PY_FIXTURE, encoding="utf-8")
    (fixture / "b.ts").write_text(TS_FIXTURE, encoding="utf-8")

    result = _run_cli(venv_python, fixture, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ast_grep_flagged_loc"] > 0
    rule_ids = {hit["rule_id"] for hit in report["ast_hits"]}
    assert "bool-comparison" in rule_ids  # python rules loaded
    assert "boolean-literal-comparison" in rule_ids  # typescript rules loaded

    pkg_dir = Path(
        subprocess.run(
            [
                str(venv_python),
                "-c",
                "import code_erosion, pathlib;"
                " print(pathlib.Path(code_erosion.__file__).parent)",
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
        ).stdout.strip()
    ).resolve()
    # The assertions below delete files under pkg_dir; it must be the venv's
    # installed package, never the repository checkout.
    assert pkg_dir.is_relative_to(venv_dir.resolve())
    rules_root = pkg_dir / "rules"
    assert (rules_root / "python").is_dir()
    assert (rules_root / "typescript").is_dir()

    # Removing a packaged rule tree turns that language's scan into a loud
    # failure - never a plausible partial score.
    shutil.rmtree(rules_root / "typescript")
    ts_only = tmp_path / "ts-only"
    ts_only.mkdir()
    (ts_only / "b.ts").write_text(TS_FIXTURE, encoding="utf-8")
    result = _run_cli(venv_python, ts_only, cwd=tmp_path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "rules for 'typescript' are missing" in result.stderr

    # An empty rule dir fails just as loudly as a missing one.
    (rules_root / "typescript").mkdir()
    result = _run_cli(venv_python, ts_only, cwd=tmp_path)
    assert result.returncode == 2
    assert "rules for 'typescript' are empty" in result.stderr

    # The intact language still scans: failure is per-language, not global.
    py_only = tmp_path / "py-only"
    py_only.mkdir()
    (py_only / "a.py").write_text(PY_FIXTURE, encoding="utf-8")
    result = _run_cli(venv_python, py_only, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ast_grep_flagged_loc"] > 0

    # No sg on PATH at all: also loud, whether the message is "not found"
    # or a foreign binary erroring out.
    bare_path = tmp_path / "bare-path"
    bare_path.mkdir()
    result = _run_cli(venv_python, py_only, cwd=tmp_path, env={"PATH": str(bare_path)})
    assert result.returncode == 2
    assert "ast-grep" in result.stderr
