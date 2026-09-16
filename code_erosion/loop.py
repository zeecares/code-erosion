"""Guarded orchestration helpers for a code-erosion refactoring loop."""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BASELINE = ".code-erosion.json"
TEST_PARTS = {"test", "tests", "spec", "specs", "__tests__"}


def discover_checks(root: Path) -> list[str]:
    """Return conservative, repo-owned checks in a stable order."""
    checks: list[str] = []
    package = root / "package.json"
    if package.exists():
        scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
        for name in ("test", "typecheck", "lint"):
            if name in scripts:
                checks.append(f"npm run {name}")
    makefile = root / "Makefile"
    if makefile.exists():
        targets = {
            line.split(":", 1)[0]
            for line in makefile.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith((" ", "\t", ".")) and ":" in line
        }
        for name in ("test", "typecheck", "lint"):
            if name in targets:
                checks.append(f"make {name}")
    if (root / "tests").is_dir() and not any("test" in item for item in checks):
        checks.append("python -m pytest")
    return checks


def changed_numstat(root: Path) -> list[tuple[int, int, str]]:
    result = subprocess.run(
        ["git", "diff", "--numstat", "HEAD", "--"], cwd=root,
        check=True, capture_output=True, text=True,
    )
    rows = []
    for line in result.stdout.splitlines():
        added, deleted, name = line.split("\t", 2)
        rows.append((int(added) if added.isdigit() else 0,
                     int(deleted) if deleted.isdigit() else 0, name))
    return rows


def evaluate(root: Path, before: dict, after: dict, checks: dict) -> dict:
    changes = changed_numstat(root)
    reasons: list[str] = []
    if not changes:
        reasons.append("executor produced no patch")
    if any(name == BASELINE or name.endswith("/" + BASELINE) for _, _, name in changes):
        reasons.append("candidate touched the committed baseline")
    weakened = [name for added, deleted, name in changes
                if deleted > added and TEST_PARTS.intersection(Path(name).parts)]
    if weakened:
        reasons.append("candidate deletes more test lines than it adds: " + ", ".join(weakened))
    if not checks.get("passed", False):
        reasons.append("repository checks did not all pass")
    before_erosion = float(before["erosion"])
    after_erosion = float(after["erosion"])
    if after_erosion >= before_erosion:
        reasons.append("erosion did not improve")
    return {
        "accepted": not reasons,
        "reasons": reasons,
        "before": {"erosion": before_erosion, "verbosity": float(before["verbosity"])},
        "after": {"erosion": after_erosion, "verbosity": float(after["verbosity"])},
        "erosion_delta": round(after_erosion - before_erosion, 6),
        "checks": checks,
        "changed_files": [name for _, _, name in changes],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="code-erosion-loop")
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover-checks")
    discover.add_argument("root", type=Path)
    final = sub.add_parser("evaluate")
    final.add_argument("root", type=Path)
    final.add_argument("--before", type=Path, required=True)
    final.add_argument("--after", type=Path, required=True)
    final.add_argument("--checks", type=Path, required=True)
    final.add_argument("--suggestions", type=Path, required=True)
    final.add_argument("--executor", required=True)
    final.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "discover-checks":
        print(json.dumps(discover_checks(args.root)))
        return 0
    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    checks = json.loads(args.checks.read_text(encoding="utf-8"))
    suggestions = json.loads(args.suggestions.read_text(encoding="utf-8"))
    result = evaluate(args.root, before, after, checks)
    result.update({
        "schema": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "executor": args.executor,
        "offender": (suggestions.get("suggestions") or [None])[0],
    })
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
