"""Optional, file-level change frequency for prioritizing erosion work."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import subprocess


class HotspotHistoryError(RuntimeError):
    """No reliable local git history is available for this scan."""


def file_change_counts(root: Path, *, days: int = 90) -> dict[str, int]:
    """Count commits touching each file in a fixed recent window, not changed lines.

    File scope is intentional: the current function's old line ranges and names
    need not match history. Renames are not followed, so this is a conservative
    signal about the current path rather than an invented per-function count.
    """
    root = root.resolve()
    try:
        repo = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, check=True, timeout=15,
        ).stdout.decode("utf-8", errors="strict").strip()
        if Path(repo).resolve() != root:
            raise HotspotHistoryError("scan the git repository root to use hotspots")
        shallow = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-shallow-repository"],
            capture_output=True, check=True, timeout=15,
        ).stdout.strip()
        if shallow == b"true":
            raise HotspotHistoryError("shallow git history; fetch full history before ranking hotspots")
        history = subprocess.run(
            ["git", "-C", str(root), "log", "--no-renames", f"--since={days}.days.ago",
             "--format=%x1e%H", "--name-only", "-z", "HEAD", "--"],
            capture_output=True, check=True, timeout=60,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        raise HotspotHistoryError(f"git history unavailable: {exc}") from exc

    counts: Counter[str] = Counter()
    for commit in history.split(b"\x1e")[1:]:
        # Git emits the hash then a NUL, followed by NUL-delimited paths.
        paths = commit.split(b"\x00")[1:]
        # --name-only inserts one separator newline after the hash.
        if paths and paths[0].startswith(b"\n"):
            paths[0] = paths[0][1:]
        for path in set(paths):
            if path.strip():
                counts[path.decode("utf-8", errors="surrogateescape")] += 1
    return dict(counts)
