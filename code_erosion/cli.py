"""Command-line interface: scan a repo, print or dump the report."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from importlib.resources import files as resource_files
from pathlib import Path

from code_erosion import baseline as baseline_mod
from code_erosion import history as history_mod
from code_erosion import suggestions as suggestions_mod
from code_erosion.core import (
    AstHit,
    ParseError,
    build_report,
    detect_clones,
    detect_trivial_wrappers,
    extract_functions,
    parse_source,
    sloc_lines,
    walk_files,
)

class RulesUnavailableError(RuntimeError):
    """The packaged ast-grep rule set cannot be loaded or executed.

    A scan that cannot run its rule set would emit a plausible-looking
    partial score, so the CLI refuses to report one at all instead of
    degrading quietly.
    """


def _rule_texts(language: str) -> list[str]:
    """Return the packaged YAML rule texts for one language, or raise.

    Rules ship as package data under ``code_erosion/rules/<language>/`` and
    are resolved through importlib.resources, so an installed wheel finds
    them inside the package rather than beside the repository checkout.
    The TypeScript directory is ``typescript``, matching the language key
    the scanner dispatches on.
    """
    rules_dir = resource_files("code_erosion").joinpath("rules").joinpath(language)
    try:
        is_dir = rules_dir.is_dir()
        rule_files = sorted(
            (entry for entry in rules_dir.iterdir() if entry.name.endswith(".yaml")),
            key=lambda entry: entry.name,
        ) if is_dir else []
    except OSError as exc:
        raise RulesUnavailableError(
            f"packaged ast-grep rules for {language!r} cannot be enumerated "
            f"under {rules_dir} ({exc}); the code-erosion install is incomplete - "
            "reinstall it rather than trust a partial score"
        ) from exc
    if not is_dir:
        raise RulesUnavailableError(
            f"packaged ast-grep rules for {language!r} are missing "
            f"(expected {rules_dir}); the code-erosion install is incomplete - "
            "reinstall it rather than trust a partial score"
        )
    if not rule_files:
        raise RulesUnavailableError(
            f"packaged ast-grep rules for {language!r} are empty under "
            f"{rules_dir}; the code-erosion install is incomplete - "
            "reinstall it rather than trust a partial score"
        )
    texts: list[str] = []
    for entry in rule_files:
        try:
            texts.append(entry.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise RulesUnavailableError(
                f"packaged ast-grep rule {entry.name!r} for {language!r} is "
                f"unreadable ({exc}); the code-erosion install is incomplete - "
                "reinstall it rather than trust a partial score"
            ) from exc
    return texts


def _ast_grep_binary() -> str:
    """Locate the ast-grep CLI, verifying the candidate really is ast-grep.

    ``sg`` is deprecated by ast-grep itself and collides with the Unix
    setgroups utility, so ``ast-grep`` is probed first and every candidate
    must answer ``--version`` as ast-grep before it is trusted with a scan.
    """
    rejected: list[str] = []
    for name in ("ast-grep", "sg"):
        candidate = shutil.which(name)
        if candidate is None:
            continue
        try:
            probe = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            rejected.append(f"{candidate} (would not run)")
            continue
        if probe.returncode == 0 and re.search(
            r"(?m)^ast-grep \d+\.\d+", probe.stdout
        ):
            return candidate
        rejected.append(f"{candidate} (not ast-grep)")
    detail = f"; rejected {', '.join(rejected)}" if rejected else ""
    raise RulesUnavailableError(
        f"ast-grep binary not found{detail}; the code-erosion install is "
        "incomplete - reinstall it rather than trust a partial score"
    )


def run_ast_grep(
    files: list[Path], language: str, warnings: list[str]
) -> list[AstHit]:
    """Run packaged ast-grep rules for one language; fail loudly if impossible."""
    if not files:
        return []
    rule_texts = _rule_texts(language)
    sg = _ast_grep_binary()
    combined = "\n".join(rule_texts)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(combined)
        rules_path = handle.name
    try:
        result = subprocess.run(
            [sg, "scan", "--json=stream", "-r", rules_path, *map(str, files)],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        raise RulesUnavailableError(f"failed to execute ast-grep: {exc}") from exc
    finally:
        Path(rules_path).unlink(missing_ok=True)
    if result.returncode != 0:
        raise RulesUnavailableError(
            f"ast-grep exited {result.returncode}; the packaged rules did not "
            f"run: {result.stderr.strip()[:300]}"
        )
    hits: list[AstHit] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            hits.append(
                AstHit(
                    file=Path(payload["file"]).resolve(),
                    line=int(payload["range"]["start"]["line"]) + 1,
                    end_line=int(payload["range"]["end"]["line"]) + 1,
                    rule_id=str(payload["ruleId"]),
                    message=str(payload.get("message", "")),
                )
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RulesUnavailableError(
                "ast-grep returned malformed JSON output; refusing to emit a "
                "partial score"
            ) from exc
    return sorted(hits, key=lambda h: (h.file.as_posix(), h.line, h.rule_id))


def scan(root: Path) -> "object":
    parsed = []
    parse_failures: list[str] = []
    warnings: list[str] = []
    files = walk_files(root)
    for path, spec in files:
        try:
            source = path.read_text(encoding="utf-8")
            tree = parse_source(source, spec)
        except (OSError, UnicodeDecodeError, ParseError) as exc:
            parse_failures.append(f"{path}: {exc}")
            continue
        sloc = sloc_lines(source, tree, spec)
        parsed.append((path, source, tree, spec, sloc))

    clones = detect_clones(parsed)
    wrappers = detect_trivial_wrappers(parsed)
    functions = []
    for path, _s, tree, spec, sloc in parsed:
        functions.extend(extract_functions(path, tree, sloc, spec))

    hits: list[AstHit] = []
    by_language: dict[str, list[Path]] = {}
    for path, _s, _t, spec, _sloc in parsed:
        by_language.setdefault(spec.name, []).append(path)
    for language, lang_files in by_language.items():
        hits.extend(run_ast_grep(lang_files, language, warnings))

    return build_report(
        root.resolve(), parsed, clones, hits, wrappers, functions,
        parse_failures, warnings,
    )


def _report_json(report) -> dict:
    data = dataclasses.asdict(report)
    data["per_file"] = [
        {**row, "verbosity": row_obj.verbosity}
        for row, row_obj in zip(data["per_file"], report.per_file)
    ]
    for key in ("root",):
        data[key] = str(data[key])
    return data


def _print_human(report, verbose: bool) -> None:
    print(f"code-erosion report: {report.root}")
    print(f"  files scanned      {report.files_scanned}")
    print(f"  total SLOC         {report.total_loc}")
    print(
        f"  verbosity          {report.verbosity:.3f}   "
        f"(union {report.verbosity_flagged_loc} = clones {report.clone_loc} "
        f"+ ast-grep {report.ast_grep_flagged_loc} "
        f"+ wrappers {report.trivial_wrapper_loc}, minus overlaps)"
    )
    print(
        f"  erosion            {report.erosion:.3f}   "
        f"(high-CC mass {report.high_cc_mass:.1f} / total {report.total_mass:.1f}; "
        f"{report.high_cc_functions} of {report.total_functions} functions CC>10)"
    )
    for warning in report.warnings:
        print(f"  warning: {warning}")
    for failure in report.parse_failures:
        print(f"  parse failure: {failure}")
    if not verbose:
        return

    print("\nPer-file verbosity (sorted by union lines):")
    header = f"  {'file':<60} {'SLOC':>6} {'clone':>6} {'ast':>6} {'wrap':>6} {'union':>6} {'verb':>6}"
    print(header)
    for row in sorted(report.per_file, key=lambda r: -r.union_lines):
        rel = row.file.relative_to(report.root) if row.file.is_relative_to(report.root) else row.file
        print(
            f"  {str(rel):<60} {row.sloc:>6} {row.clone_lines:>6} "
            f"{row.ast_lines:>6} {row.wrapper_lines:>6} {row.union_lines:>6} "
            f"{row.verbosity:>6.3f}"
        )

    print("\nPer-function erosion mass (sorted by mass):")
    print(f"  {'function':<40} {'file:line':<50} {'SLOC':>5} {'CC':>4} {'mass':>8} {'high':>5}")
    for func in sorted(report.functions, key=lambda f: -f.mass):
        rel = func.file.relative_to(report.root) if func.file.is_relative_to(report.root) else func.file
        print(
            f"  {func.name[:40]:<40} {f'{rel}:{func.start_line}':<50} "
            f"{func.sloc:>5} {func.cc:>4} {func.mass:>8.1f} "
            f"{'HIGH' if func.is_high_cc else '':>5}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="code-erosion",
        description="Measure code verbosity and structural erosion (SlopCodeBench metrics).",
    )
    parser.add_argument("path", type=Path, help="Repo, directory, or file to scan")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Per-file verbosity table and per-function mass table")
    parser.add_argument("--json", action="store_true", help="Emit the full report as JSON")
    parser.add_argument("--write-baseline", type=Path, metavar="PATH",
                        help="Write a baseline snapshot of this scan to PATH")
    parser.add_argument("--check-baseline", type=Path, metavar="PATH",
                        help="Diff this scan against the baseline at PATH (CI gate)")
    parser.add_argument("--threshold", type=float, default=baseline_mod.DEFAULT_THRESHOLD,
                        help="Allowed absolute erosion regression before the gate fails "
                             f"(default: {baseline_mod.DEFAULT_THRESHOLD})")
    parser.add_argument("--informational", action="store_true",
                        help="Report the diff but never fail, even past the threshold")
    parser.add_argument("--comment-out", type=Path, metavar="PATH",
                        help="Write the markdown gate report to PATH (for PR comments)")
    parser.add_argument("--record-history", type=Path, metavar="PATH",
                        help="Append this scan as one JSONL record to PATH (trend history)")
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA", "unknown"),
                        help="Commit SHA recorded by --record-history "
                             "(default: $GITHUB_SHA or 'unknown')")
    parser.add_argument("--date", default=None, metavar="ISO_DATE",
                        help="Timestamp recorded by --record-history (default: now, UTC)")
    parser.add_argument("--write-badge", type=Path, metavar="PATH",
                        help="Write a shields.io endpoint badge JSON for this scan to PATH")
    parser.add_argument("--history", type=Path, metavar="PATH",
                        help="Committed history file; adds a trend section to the markdown gate report")
    parser.add_argument("--suggestions-out", type=Path, metavar="PATH",
                        help="Write ranked, machine-readable refactoring suggestions as JSON")
    parser.add_argument("--agent-instructions-out", type=Path, metavar="PATH",
                        help="Write a ready-to-run refactoring agent prompt")
    parser.add_argument("--suggestions-markdown-out", type=Path, metavar="PATH",
                        help="Write the ranked suggestions as markdown")
    parser.add_argument("--suggestions-top", type=int, default=5, metavar="N",
                        help="Number of high-CC offenders to suggest (default: 5)")
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"path does not exist: {args.path}", file=sys.stderr)
        return 2
    try:
        report = scan(args.path)
    except RulesUnavailableError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if report.files_scanned == 0:
        print(f"no Python/TypeScript files could be parsed at {args.path}", file=sys.stderr)
        return 2
    report_json = _report_json(report)
    suggestions = suggestions_mod.build_suggestions(report_json, top_n=args.suggestions_top)
    suggestions_mod.write_outputs(suggestions, args.suggestions_out, args.agent_instructions_out)
    if args.suggestions_markdown_out:
        args.suggestions_markdown_out.write_text(
            suggestions_mod.render_markdown(suggestions), encoding="utf-8"
        )
    if args.json:
        json.dump(report_json, sys.stdout, indent=2, default=str)
        print()
    elif not args.check_baseline:
        _print_human(report, args.verbose)

    if args.record_history:
        record = history_mod.history_record(report_json, sha=args.sha, date=args.date)
        history_mod.append_history(args.record_history, record)
        print(
            f"history: recorded erosion {record['erosion']:.3f} "
            f"@ {record['sha'][:7]} in {args.record_history}"
        )

    if args.write_badge:
        badge = history_mod.write_badge(args.write_badge, report_json)
        print(f"badge written to {args.write_badge} (erosion {badge['message']}, {badge['color']})")

    if args.write_baseline:
        snapshot = baseline_mod.write_baseline(_report_json(report), args.write_baseline)
        print(
            f"baseline written to {args.write_baseline} "
            f"({snapshot['metrics']['files_scanned']} files, "
            f"{len(snapshot['functions'])} functions)"
        )

    if args.check_baseline:
        if not args.check_baseline.exists():
            print(f"baseline not found: {args.check_baseline}", file=sys.stderr)
            return 2
        result = baseline_mod.diff_against_baseline(
            baseline_mod.load_baseline(args.check_baseline),
            report_json,
            args.threshold,
        )
        mode = "informational" if args.informational else "gate"
        trend = ""
        if args.history:
            trend = history_mod.render_trend_markdown(history_mod.load_history(args.history))
        markdown = baseline_mod.render_markdown(
            result, baseline_path=str(args.check_baseline), mode=mode, trend=trend
        )
        if args.comment_out:
            args.comment_out.write_text(markdown, encoding="utf-8")
        if not args.json:
            _print_human(report, args.verbose)
            print()
            print(markdown)
        if not result.gate_pass and not args.informational:
            print(
                f"code-erosion gate: erosion regressed {result.erosion_delta:+.3f} "
                f"(threshold +{args.threshold:.3f})",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
