from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path


def test_wheel_contains_every_bundled_rule(tmp_path: Path) -> None:
    """Build the distributable and compare its rules to the source tree exactly."""
    repo = Path(__file__).resolve().parents[1]
    wheel_dir = tmp_path / "wheel"
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
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(wheel_dir.glob("code_erosion-*.whl"))
    assert len(wheels) == 1

    expected = {
        path.relative_to(repo).as_posix()
        for path in (repo / "rules").glob("*/*.yaml")
    }
    assert expected
    assert any(path.startswith("rules/python/") for path in expected)
    assert any(path.startswith("rules/ts/") for path in expected)

    with zipfile.ZipFile(wheels[0]) as archive:
        actual = {
            name[name.index("rules/") :]
            for name in archive.namelist()
            if "rules/" in name and name.endswith(".yaml")
        }
    assert actual == expected
