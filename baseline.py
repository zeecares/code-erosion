"""Baseline snapshots and CI gating for code-erosion reports.

A baseline is a committed JSON snapshot of one scan (per-file verbosity and
per-function erosion mass, keyed by repo-relative path). On a PR, the current
scan is diffed against it; the gate fails when the repo-level erosion regresses
beyond a threshold.

Function identity is ``relative/path::function_name``: stable across the line
drift that normal edits cause. Two same-named functions in one file share a
key; the higher-mass one wins and the collision is recorded in the snapshot's
``collisions`` list (rare, and harmless for a regression gate).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

BASELINE_SCHEMA = 2
DEFAULT_THRESHOLD = 0.01
COMMENT_MARKER = "<!-- code-erosion-report -->"


# ---------------------------------------------------------------- snapshot


def _relativize(file: str, root: str) -> str:
    root = root.rstrip("/")
    if root and file.startswith(root + "/"):
        return file[len(root) + 1 :]
    return file


def _corpus_metrics(report: dict, name: str) -> dict:
    corpus = report.get("corpora", {}).get(name, {})
    fallback = report if name == "production" else {}
    return {
        "verbosity": float(corpus.get("verbosity", fallback.get("verbosity", 0.0))),
        "erosion": float(corpus.get("erosion", fallback.get("erosion", 0.0))),
        "total_loc": int(corpus.get("total_loc", fallback.get("total_loc", 0))),
        "files_scanned": int(corpus.get("files_scanned", fallback.get("files_scanned", 0))),
        "total_functions": int(corpus.get("total_functions", fallback.get("total_functions", 0))),
        "high_cc_functions": int(corpus.get("high_cc_functions", fallback.get("high_cc_functions", 0))),
    }


def snapshot_from_report(report: dict) -> dict:
    """Build a portable baseline dict from a ``--json`` report."""
    root = str(report.get("root", ""))
    files: dict[str, dict] = {}
    for row in report.get("per_file", []):
        rel = _relativize(str(row["file"]), root)
        files[rel] = {
            "sloc": int(row["sloc"]),
            "verbosity": round(float(row["verbosity"]), 4),
        }

    functions: dict[str, dict] = {}
    collisions: list[str] = []
    for func in report.get("functions", []):
        rel = _relativize(str(func["file"]), root)
        key = f"{rel}::{func['name']}"
        sloc = int(func["sloc"])
        cc = int(func["cc"])
        mass = cc * math.sqrt(sloc)
        entry = {
            "cc": cc,
            "sloc": sloc,
            "mass": round(mass, 1),
            "high": cc > 10,
            "line": int(func.get("start_line", 0)),
        }
        if key in functions:
            collisions.append(key)
            if entry["mass"] <= functions[key]["mass"]:
                continue
        functions[key] = entry

    return {
        "schema": BASELINE_SCHEMA,
        "tool": "code-erosion",
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metrics": {
            "verbosity": float(report["verbosity"]),
            "erosion": float(report["erosion"]),
            "total_loc": int(report["total_loc"]),
            "files_scanned": int(report["files_scanned"]),
            "total_functions": int(report["total_functions"]),
            "high_cc_functions": int(report["high_cc_functions"]),
            "production": _corpus_metrics(report, "production"),
            "test": _corpus_metrics(report, "test"),
        },
        "files": files,
        "functions": functions,
        "collisions": collisions,
    }


def write_baseline(report: dict, path: Path) -> dict:
    snapshot = snapshot_from_report(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    return snapshot


def load_baseline(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "metrics" not in data:
        raise ValueError(f"not a code-erosion baseline: {path}")
    if data.get("schema") != BASELINE_SCHEMA:
        raise ValueError(
            f"baseline schema {data.get('schema', 'unknown')} is incompatible with schema "
            f"{BASELINE_SCHEMA}; regenerate it so the gate can compare production separately"
        )
    data.setdefault("files", {})
    data.setdefault("functions", {})
    return data


# ---------------------------------------------------------------- diff


@dataclass
class CheckResult:
    baseline: dict
    current: dict
    threshold: float
    erosion_delta: float = 0.0
    verbosity_delta: float = 0.0
    combined_erosion_delta: float = 0.0
    test_erosion_delta: float = 0.0
    new_high_cc: list[str] = field(default_factory=list)
    resolved_high_cc: list[str] = field(default_factory=list)
    mass_movers_up: list[tuple[str, float, float]] = field(default_factory=list)
    mass_movers_down: list[tuple[str, float, float]] = field(default_factory=list)
    verbosity_movers: list[tuple[str, float, float]] = field(default_factory=list)
    has_base_detail: bool = True

    @property
    def gate_pass(self) -> bool:
        return self.erosion_delta <= self.threshold + 1e-9


def diff_against_baseline(baseline: dict, current_report: dict, threshold: float) -> CheckResult:
    current = snapshot_from_report(current_report)
    base_m = baseline["metrics"]
    cur_m = current["metrics"]
    result = CheckResult(
        baseline=baseline,
        current=current,
        threshold=threshold,
        erosion_delta=cur_m["production"]["erosion"] - base_m["production"]["erosion"],
        verbosity_delta=cur_m["production"]["verbosity"] - base_m["production"]["verbosity"],
        combined_erosion_delta=cur_m["erosion"] - base_m["erosion"],
        test_erosion_delta=cur_m["test"]["erosion"] - base_m["test"]["erosion"],
        has_base_detail=bool(baseline.get("functions") or baseline.get("files")),
    )

    base_funcs = baseline["functions"]
    cur_funcs = current["functions"]
    for key, entry in sorted(cur_funcs.items()):
        base_entry = base_funcs.get(key)
        if entry["high"] and (base_entry is None or not base_entry["high"]):
            result.new_high_cc.append(key)
    for key, entry in sorted(base_funcs.items()):
        cur_entry = cur_funcs.get(key)
        if entry["high"] and (cur_entry is None or not cur_entry["high"]):
            result.resolved_high_cc.append(key)

    movers: list[tuple[str, float]] = []
    for key, entry in cur_funcs.items():
        if key in base_funcs:
            movers.append((key, entry["mass"] - base_funcs[key]["mass"]))
    movers_up = sorted((m for m in movers if m[1] >= 1.0), key=lambda m: -m[1])[:5]
    movers_down = sorted((m for m in movers if m[1] <= -1.0), key=lambda m: m[1])[:5]
    result.mass_movers_up = [(k, base_funcs[k]["mass"], cur_funcs[k]["mass"]) for k, _ in movers_up]
    result.mass_movers_down = [(k, base_funcs[k]["mass"], cur_funcs[k]["mass"]) for k, _ in movers_down]

    base_files = baseline["files"]
    cur_files = current["files"]
    vmovers = [
        (path, cur["verbosity"] - base_files[path]["verbosity"])
        for path, cur in cur_files.items()
        if path in base_files
    ]
    top_v = sorted(vmovers, key=lambda m: -abs(m[1]))[:5]
    result.verbosity_movers = [
        (p, base_files[p]["verbosity"], cur_files[p]["verbosity"]) for p, _ in top_v if abs(_) >= 0.005
    ]
    return result


# ---------------------------------------------------------------- markdown


def _fmt_delta(value: float, digits: int = 3) -> str:
    return f"{value:+.{digits}f}"


def render_markdown(result: CheckResult, *, baseline_path: str, mode: str, trend: str = "") -> str:
    base_m = result.baseline["metrics"]
    cur_m = result.current["metrics"]
    lines = [
        COMMENT_MARKER,
        "## code-erosion report",
        "",
        "| metric | baseline | this PR | delta |",
        "|---|---|---|---|",
        f"| production verbosity | {base_m['production']['verbosity']:.3f} | {cur_m['production']['verbosity']:.3f} | {_fmt_delta(result.verbosity_delta)} |",
        f"| production erosion (gate) | {base_m['production']['erosion']:.3f} | {cur_m['production']['erosion']:.3f} | {_fmt_delta(result.erosion_delta)} |",
        f"| test/spec erosion (diagnostic) | {base_m['test']['erosion']:.3f} | {cur_m['test']['erosion']:.3f} | {_fmt_delta(result.test_erosion_delta)} |",
        f"| combined erosion (diagnostic) | {base_m['erosion']:.3f} | {cur_m['erosion']:.3f} | {_fmt_delta(result.combined_erosion_delta)} |",
        "",
    ]
    if mode == "informational":
        lines.append(f"**Mode: informational** - nothing fails. Production erosion moved {_fmt_delta(result.erosion_delta)} (gate threshold {result.threshold:.3f}).")
    elif result.gate_pass:
        lines.append(f"**Gate: PASS** - production erosion regression {_fmt_delta(result.erosion_delta)} is within the +{result.threshold:.3f} threshold.")
    else:
        lines.append(f"**Gate: FAIL** - production erosion regressed {_fmt_delta(result.erosion_delta)}, beyond the +{result.threshold:.3f} threshold.")
    lines.append("")

    if not result.has_base_detail:
        lines += [
            "_Baseline has repo-level metrics only (seed file) - per-file and per-function",
            "movement appears after the next baseline refresh._",
            "",
        ]
    else:
        lines.append(f"**New high-complexity functions (CC > 10):** {len(result.new_high_cc)}")
        for key in result.new_high_cc[:10]:
            entry = result.current["functions"][key]
            lines.append(f"- `{key}` (CC {entry['cc']}, SLOC {entry['sloc']}, mass {entry['mass']:.0f})")
        if result.resolved_high_cc:
            lines.append("")
            lines.append(f"**Resolved high-complexity functions:** {len(result.resolved_high_cc)}")
            for key in result.resolved_high_cc[:10]:
                lines.append(f"- `{key}`")
        if result.mass_movers_up:
            lines += ["", "**Erosion mass movers (up):**", "", "| function | was | now |", "|---|---|---|"]
            for key, was, now in result.mass_movers_up:
                lines.append(f"| `{key}` | {was:.0f} | {now:.0f} |")
        if result.mass_movers_down:
            lines += ["", "**Erosion mass movers (down):**", "", "| function | was | now |", "|---|---|---|"]
            for key, was, now in result.mass_movers_down:
                lines.append(f"| `{key}` | {was:.0f} | {now:.0f} |")
        if result.verbosity_movers:
            lines += ["", "**Biggest verbosity movement (per file):**", "", "| file | was | now |", "|---|---|---|"]
            for path, was, now in result.verbosity_movers:
                lines.append(f"| `{path}` | {was:.3f} | {now:.3f} |")
    if trend:
        lines.append(trend.rstrip("\n"))
    lines += [
        "",
        "---",
        f"Baseline `{baseline_path}`: {base_m['files_scanned']} files, {base_m['total_loc']} SLOC, "
        f"created {result.baseline.get('created', 'unknown')}. If this movement is intended, refresh the baseline "
        "(`code-erosion . --write-baseline " + baseline_path + "`) and commit it in this PR - the gate then "
        "passes and the reviewer sees the new baseline in the diff. The score is a tripwire, not a target: "
        "please do not tune code to the metric.",
    ]
    return "\n".join(lines) + "\n"
