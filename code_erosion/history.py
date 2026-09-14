"""Score-per-commit trend history and README badge support (R4).

History is a committed JSONL file (``.code-erosion-history.jsonl`` by
default): one JSON object per default-branch scan. The GitHub Action appends
on push events and commits the file back, so the repo carries its own trend
data and any tool can chart it. On PRs, when history exists, the report
comment gains a trend section.

The badge file is a shields.io endpoint payload (https://shields.io/endpoint):
commit it and point a README image at
``https://img.shields.io/endpoint?url=<raw-url-of-the-badge-json>``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

HISTORY_SCHEMA = 1
DEFAULT_HISTORY_PATH = ".code-erosion-history.jsonl"
DEFAULT_BADGE_PATH = ".code-erosion-badge.json"

# Erosion reference bands (Python-calibrated, from the SlopCodeBench post):
# human ~0.31, agent ~0.68. Badge colors follow the bands.
_BAND_GREEN = 0.35
_BAND_YELLOW = 0.55
_BAND_ORANGE = 0.68


def history_record(report: dict, *, sha: str = "unknown", date: str | None = None) -> dict:
    """One trend record from a ``--json`` report dict."""
    return {
        "schema": HISTORY_SCHEMA,
        "sha": sha,
        "date": date or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verbosity": round(float(report["verbosity"]), 4),
        "erosion": round(float(report["erosion"]), 4),
        "total_loc": int(report["total_loc"]),
        "files_scanned": int(report["files_scanned"]),
        "high_cc_functions": int(report["high_cc_functions"]),
    }


def append_history(path: Path, record: dict) -> None:
    """Append one record to the JSONL history file, creating it if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")


def load_history(path: Path) -> list[dict]:
    """Read a committed history file, oldest first; malformed lines are skipped."""
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and "erosion" in entry and "date" in entry:
            entries.append(entry)
    entries.sort(key=lambda e: (str(e["date"]), str(e.get("sha", ""))))
    return entries


def trend_direction(delta: float, *, eps: float = 0.005) -> str:
    """Classify an erosion delta: below -eps improving, above +eps worsening."""
    if delta < -eps:
        return "improving"
    if delta > eps:
        return "worsening"
    return "flat"


def render_trend_markdown(entries: list[dict], *, limit: int = 5) -> str:
    """Trend section for the PR comment. Empty string when there is no history."""
    if not entries:
        return ""
    window = entries[-limit:]
    values = " → ".join(f"{float(e['erosion']):.3f}" for e in window)
    delta = float(window[-1]["erosion"]) - float(window[0]["erosion"])
    direction = trend_direction(delta)
    plural = "s" if len(window) != 1 else ""
    lines = [
        "",
        f"**Trend on the default branch (last {len(window)} scan{plural}):** "
        f"{direction} ({values})",
        "",
        "| date | commit | verbosity | erosion |",
        "|---|---|---|---|",
    ]
    for e in window:
        sha = str(e.get("sha", "unknown"))[:7]
        lines.append(
            f"| {str(e['date'])[:10]} | `{sha}` | "
            f"{float(e['verbosity']):.3f} | {float(e['erosion']):.3f} |"
        )
    lines.append("")
    return "\n".join(lines)


def badge_from_report(report: dict) -> dict:
    """shields.io endpoint payload for the current erosion score."""
    erosion = float(report["erosion"])
    if erosion <= _BAND_GREEN:
        color = "green"
    elif erosion <= _BAND_YELLOW:
        color = "yellow"
    elif erosion <= _BAND_ORANGE:
        color = "orange"
    else:
        color = "red"
    return {
        "schemaVersion": 1,
        "label": "code erosion",
        "message": f"{erosion:.3f}",
        "color": color,
    }


def write_badge(path: Path, report: dict) -> dict:
    """Write the shields.io endpoint badge JSON for this scan."""
    badge = badge_from_report(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(badge, indent=2) + "\n", encoding="utf-8")
    return badge
