"""Turn high-complexity functions into an agent-ready refactoring worklist."""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA_VERSION = 1
DISCIPLINE = [
    "Optimize for a deep module: a small public interface hiding substantial implementation detail.",
    "Decompose by responsibility and stable seams, not by line count or to make the score fall.",
    "Keep policy decisions near their data; avoid thin wrappers that only move complexity around.",
    "Preserve behavior first. Add characterization or differential tests before changing structure.",
    "Re-scan after the refactor and report both score movement and any metric-geometry caveat.",
]

_DRIVER_HINTS = {
    "if_statement": "conditional branches: group related policy behind one named decision",
    "elif_clause": "conditional branches: replace repeated case handling with a table or strategy only when it clarifies policy",
    "for_statement": "loops: extract the operation only if it forms a coherent deep module",
    "while_statement": "loops: separate termination/control policy from the work performed",
    "except_clause": "error paths: centralize recovery policy without hiding failures",
    "conditional_expression": "inline conditionals: name non-obvious decisions",
    "switch_case": "case branches: look for a cohesive dispatch boundary",
    "switch_default": "case branches: make fallback policy explicit",
    "catch_clause": "error paths: centralize recovery policy without hiding failures",
    "ternary_expression": "inline conditionals: name non-obvious decisions",
    "logical_and": "compound conditions: name the business predicate if it carries meaning",
    "logical_or": "compound conditions: name the business predicate if it carries meaning",
    "nullish_coalescing": "fallback values: name the defaulting policy if it carries meaning",
}


def build_suggestions(report: dict, *, top_n: int = 5) -> dict:
    top_n = max(0, top_n)
    root = Path(report["root"])
    total_mass = float(report.get("total_mass", 0.0))
    ranked = []
    for func in report.get("functions", []):
        cc, sloc = int(func["cc"]), int(func["sloc"])
        if cc <= 10:
            continue
        path = Path(func["file"])
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        mass = cc * sloc**0.5
        drivers = []
        for driver in func.get("complexity_drivers", []):
            kind = driver["kind"]
            drivers.append({
                "kind": kind,
                "line": int(driver["line"]),
                "hint": _DRIVER_HINTS.get(kind, "decision point: find the responsibility or policy seam"),
            })
        ranked.append({
            "rank": 0,
            "function": func["name"],
            "path": rel,
            "start_line": int(func["start_line"]),
            "end_line": int(func["end_line"]),
            "anchor": f"{rel}:{func['start_line']}-{func['end_line']}",
            "language": func["language"],
            "sloc": sloc,
            "cyclomatic_complexity": cc,
            "erosion_mass": round(mass, 3),
            "share_of_total_mass": round(mass / total_mass, 4) if total_mass else 0,
            "complexity_drivers": drivers,
            "guidance": DISCIPLINE,
            "verification": [
                "Run the existing focused tests before and after the change.",
                "For parser, serializer, evaluator, or other pure logic, add a differential check over representative and fuzz inputs.",
                "Run the full test/typecheck/lint suite required by the repository.",
                "Run code-erosion before and after; treat the score as evidence, not the target.",
            ],
        })
    ranked.sort(key=lambda item: (-item["erosion_mass"], item["path"], item["start_line"]))
    ranked = ranked[:top_n]
    for index, item in enumerate(ranked, 1):
        item["rank"] = index
    return {
        "schema": SCHEMA_VERSION,
        "repo_root": str(root),
        "metrics": {"verbosity": report["verbosity"], "erosion": report["erosion"]},
        "selection": {"top_n": top_n, "threshold": "CC > 10", "count": len(ranked)},
        "discipline": DISCIPLINE,
        "suggestions": ranked,
    }


def _markdown_text(value: object) -> str:
    """Render repository-controlled text inert in GitHub Markdown."""
    text = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in str(value))
    text = text.replace("@", "@\u200b")
    for char in "\\`*_{}[]<>()#+-.!|":
        text = text.replace(char, "\\" + char)
    return text


def render_markdown(payload: dict) -> str:
    out = ["## Refactoring suggestions", "", "Ranked by erosion mass. Fix the code, not the score.", ""]
    if not payload["suggestions"]:
        return "\n".join(out + ["No functions exceed CC 10.", ""])
    for item in payload["suggestions"]:
        function = _markdown_text(item["function"])
        anchor = _markdown_text(item["anchor"])
        out += [
            f"### {item['rank']}. `{function}` - `{anchor}`",
            "",
            f"CC {item['cyclomatic_complexity']} · {item['sloc']} SLOC · mass {item['erosion_mass']:.1f} · {item['share_of_total_mass']:.1%} of total mass",
            "",
        ]
        if item["complexity_drivers"]:
            out.append("Complexity drivers:")
            for driver in item["complexity_drivers"][:8]:
                out.append(f"- line {driver['line']}: `{driver['kind']}` - {driver['hint']}")
            out.append("")
        out += ["Refactor discipline:"] + [f"- {line}" for line in item["guidance"][:3]] + [""]
    return "\n".join(out)


def render_agent_instructions(payload: dict) -> str:
    items = payload["suggestions"]
    lines = [
        "# code-erosion refactoring run", "",
        "Refactor the ranked offenders below. Preserve behavior; the metric is a tripwire and prioritization signal, not the goal.", "",
        "## Operating discipline",
    ] + [f"- {line}" for line in DISCIPLINE] + ["", "## Ranked worklist"]
    for item in items:
        function = _markdown_text(item["function"])
        anchor = _markdown_text(item["anchor"])
        lines += [
            f"### {item['rank']}. {function} ({anchor})",
            f"Current: CC {item['cyclomatic_complexity']}, {item['sloc']} SLOC, erosion mass {item['erosion_mass']:.1f}.",
        ]
        for driver in item["complexity_drivers"][:8]:
            lines.append(f"- Line {driver['line']} `{driver['kind']}`: {driver['hint']}")
        lines.append("")
    lines += [
        "## Execution contract", "",
        "1. Inspect the target and its callers/tests. State the responsibility seam before editing.",
        "2. Add characterization tests where behavior is not already pinned.",
        "3. Refactor one coherent target at a time. Do not split functions mechanically or add pass-through wrappers.",
        "4. Discover the repository's documented test, typecheck, and lint commands; run the focused checks after each target, then every applicable full check.",
        "5. Re-run `code-erosion . --suggestions-out code-erosion-suggestions.json --agent-instructions-out code-erosion-agent-instructions.md`.",
        "6. Open a PR describing the responsibility change, behavior proof, before/after erosion and CC, and any score caveat.",
        "7. Stop rather than weaken tests, change product behavior, or regenerate the baseline merely to pass the gate.", "",
    ]
    return "\n".join(lines)


def write_outputs(payload: dict, suggestions_path: Path | None, instructions_path: Path | None) -> None:
    if suggestions_path:
        suggestions_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if instructions_path:
        instructions_path.write_text(render_agent_instructions(payload), encoding="utf-8")
