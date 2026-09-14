"""Command-line interface: scan a repo, print or dump the report."""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from code_erosion import baseline as baseline_mod
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

_RULES_ROOT = Path(__file__).resolve().parent.parent / "rules"


def run_ast_grep(
    files: list[Path], language: str, warnings: list[str]
) -> list[AstHit]:
    """Run bundled ast-grep rules for one language; degrade gracefully."""
    rules_dir = _RULES_ROOT / language
    if not files or not rules_dir.is_dir():
        return []
    rule_files = sorted(rules_dir.glob("*.yaml"))
    if not rule_files:
        return []
    sg = shutil.which("sg") or shutil.which("ast-grep")
    if sg is None:
        warnings.append(
            "ast-grep binary not found; AST-rule component skipped "
            "(verbosity covers clones + trivial wrappers only)"
        )
        return []
    combined = "\n".join(p.read_text(encoding="utf-8") for p in rule_files)
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
        )
    except OSError as exc:
        warnings.append(f"failed to execute ast-grep: {exc}")
        return []
    finally:
        Path(rules_path).unlink(missing_ok=True)
    if result.returncode != 0:
        warnings.append(
            f"ast-grep exited {result.returncode}: {result.stderr.strip()[:300]}"
        )
        return []
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
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            warnings.append("failed to parse one ast-grep output line")
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
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"path does not exist: {args.path}", file=sys.stderr)
        return 2
    report = scan(args.path)
    if report.files_scanned == 0:
        print(f"no Python/TypeScript files could be parsed at {args.path}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(_report_json(report), sys.stdout, indent=2, default=str)
        print()
    elif not args.check_baseline:
        _print_human(report, args.verbose)

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
            _report_json(report),
            args.threshold,
        )
        mode = "informational" if args.informational else "gate"
        markdown = baseline_mod.render_markdown(
            result, baseline_path=str(args.check_baseline), mode=mode
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
