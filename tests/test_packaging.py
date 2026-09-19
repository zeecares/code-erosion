from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def build_wheel(wheel_dir: Path) -> Path:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(wheel_dir.glob("code_erosion-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def test_wheel_contains_every_bundled_rule(tmp_path: Path) -> None:
    """Build the distributable and compare its rules to the source tree exactly."""
    wheel = build_wheel(tmp_path / "wheel")

    expected = {
        path.relative_to(REPO).as_posix()
        for path in (REPO / "code_erosion" / "rules").glob("*/*.yaml")
    }
    assert expected
    assert any(path.startswith("code_erosion/rules/python/") for path in expected)
    assert any(path.startswith("code_erosion/rules/typescript/") for path in expected)

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
    actual = {
        name
        for name in names
        if name.startswith("code_erosion/rules/") and name.endswith(".yaml")
    }
    assert actual == expected
    # The old data-files mechanism dropped the rules outside the package,
    # where the runtime never looked. It must not come back.
    assert not any(".data/" in name for name in names)
