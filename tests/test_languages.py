"""Per-language golden fixtures: JavaScript parity and Python semantics."""

from pathlib import Path

import pytest

from code_erosion import suggestions
from code_erosion.cli import scan
from code_erosion.core import (
    ParseError,
    extract_functions,
    parse_source,
    sloc_lines,
    walk_files,
)
from code_erosion.languages import PYTHON, TYPESCRIPT, spec_for_suffix


def symbols(source: str, language, name="sample.ts"):
    tree = parse_source(source, language)
    return extract_functions(Path(name), tree, sloc_lines(source, tree, language), language)


# ----------------------------------------------------------------- JS routing


def test_js_suffixes_route_to_the_typescript_engine(tmp_path: Path):
    for suffix in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
        (tmp_path / f"f{suffix}").write_text("const x = 1;\n")
    (tmp_path / "notes.txt").write_text("not code\n")
    routed = {p.name: spec.name for p, spec in walk_files(tmp_path)}
    assert routed == {
        "f.js": "typescript", "f.jsx": "typescript", "f.mjs": "typescript",
        "f.cjs": "typescript", "f.ts": "typescript", "f.tsx": "typescript",
    }
    assert spec_for_suffix(".js") is TYPESCRIPT
    assert spec_for_suffix(".py") is PYTHON
    assert spec_for_suffix(".txt") is None


# ----------------------------------------------------------------- JS parsing


def test_commonjs_and_esm_variants_parse():
    cjs = "const fs = require('fs');\nmodule.exports = { read: (p) => fs.readFileSync(p) };\n"
    mjs = "export const load = async (u) => (await fetch(u)).json();\n"
    for source in (cjs, mjs):
        funcs = symbols(source, TYPESCRIPT)
        assert funcs  # parsed and functions extracted


def test_jsx_parses_in_js_and_jsx_files():
    component = """export default function App({ user }) {
  return <main>{user ? <h1>Hi {user.name}</h1> : <h1>Login</h1>}</main>;
}
"""
    # .jsx is handled by the TSX retry; the same retry rescues JSX inside .js.
    funcs = symbols(component, TYPESCRIPT, name="app.jsx")
    assert [f.name for f in funcs] == ["App"]
    assert funcs[0].cc == 2  # the ternary


def test_parse_failure_raises():
    with pytest.raises(ParseError):
        parse_source("def broken(:\n", PYTHON)


# ------------------------------------------------------- JS CC/driver goldens


def test_js_cc_golden_lines_and_kinds():
    source = """function classify(score, flags) {
  if (score > 90 && flags.ready) {
    return "excellent";
  } else if (score > 70 || flags.override) {
    return "good";
  }
  switch (flags.mode) {
    case "strict":
      return "strict";
    case "loose":
      return "loose";
    default:
      return score ?? 0 ? "fallback" : "none";
  }
}
"""
    (classify,) = symbols(source, TYPESCRIPT)
    assert classify.cc == 9
    assert classify.complexity_drivers == (
        {"kind": "ternary_expression", "line": 13},
        {"kind": "nullish_coalescing", "line": 13},
        {"kind": "switch_case", "line": 10},
        {"kind": "switch_case", "line": 8},
        {"kind": "if_statement", "line": 2},
        {"kind": "if_statement", "line": 4},
        {"kind": "logical_or", "line": 4},
        {"kind": "logical_and", "line": 2},
    )


# ------------------------------------------------------- JS anonymous naming


def test_js_anonymous_callables_take_binding_names():
    source = """const helper = (x) => x * 2;
export default function () { return helper(1); }
obj.method = function () { return helper(2); };
exports.f = () => helper(3);
const cfg = { build: () => helper(4) };
[1].map((n) => helper(n));
"""
    names = {f.name for f in symbols(source, TYPESCRIPT)}
    assert names == {
        "helper", "<default export>", "obj.method", "exports.f", "build", "<anonymous>",
    }


# ------------------------------------------------------- Python semantics


def test_python_boolean_operators_split_into_logical_kinds():
    source = """def pick(a, b, c):
    if a and b or c:
        return a
"""
    (pick,) = symbols(source, PYTHON, name="s.py")
    kinds = [d["kind"] for d in pick.complexity_drivers]
    assert kinds == ["if_statement", "logical_or", "logical_and"]


def test_python_nested_defs_own_their_drivers_lambdas_do_not():
    source = """def outer(xs):
    def nested(x):
        if x:
            return x
    cb = lambda q: q if q else None
    return [nested(x) for x in xs if x]
"""
    outer, nested = symbols(source, PYTHON, name="s.py")
    # Nested def decisions belong to the nested callable's drivers.
    assert nested.complexity_drivers == ({"kind": "if_statement", "line": 3},)
    # Lambdas are not callables in the grammar mapping, so the lambda's
    # conditional_expression stays attributed to the enclosing function.
    outer_kinds = [d["kind"] for d in outer.complexity_drivers]
    assert outer_kinds == ["list_comprehension", "if_clause", "conditional_expression"]
    # CC deliberately stays recursive (published-metric continuity): the
    # nested def's if, the lambda's ternary, the comprehension and its filter.
    assert outer.cc == 5
    assert nested.cc == 2


def test_python_specific_driver_hints_exist():
    report = {
        "root": "/repo", "verbosity": 0.1, "erosion": 0.5, "total_mass": 100.0,
        "functions": [{
            "name": "f", "file": "/repo/a.py", "start_line": 1, "end_line": 40,
            "sloc": 40, "cc": 15, "language": "python",
            "complexity_drivers": [
                {"kind": "assert_statement", "line": 3},
                {"kind": "list_comprehension", "line": 5},
                {"kind": "if_clause", "line": 5},
                {"kind": "generator_expression", "line": 7},
                {"kind": "logical_and", "line": 9},
            ],
        }],
    }
    drivers = suggestions.build_suggestions(report)["suggestions"][0]["complexity_drivers"]
    hints = {d["kind"]: d["hint"] for d in drivers}
    assert hints["assert_statement"].startswith("invariants:")
    assert hints["list_comprehension"].startswith("comprehensions:")
    assert hints["if_clause"].startswith("comprehension filters:")
    assert hints["generator_expression"].startswith("comprehensions:")
    assert hints["logical_and"].startswith("compound conditions:")
    assert all("decision point" not in h for h in hints.values())


# ------------------------------------------------------- mixed-language scan


def test_scan_handles_a_mixed_python_and_js_repo(tmp_path: Path):
    (tmp_path / "a.py").write_text("def f(x):\n    return x if x else None\n")
    (tmp_path / "b.js").write_text("export const g = (y) => (y ? y : null);\n")
    (tmp_path / "c.cjs").write_text("module.exports = function h() { return 1; };\n")
    report = scan(tmp_path)
    assert report.parse_failures == []
    assert report.files_scanned == 3
    languages = {f.language for f in report.functions}
    assert languages == {"python", "typescript"}
    names = {f.name for f in report.functions}
    assert {"f", "g", "h"} <= names

# ------------------------------------------------------- corpus classification

@pytest.mark.parametrize("path", [
    "tests/unit/test_api.py", "test/integration/api_test.py", "spec/model_spec.py",
    "specs/model.py", "src/__tests__/widget.ts", "src/widget.test.ts",
    "src/widget.spec.tsx", "test_root.py", "thing_test.py",
])
def test_test_corpus_patterns(path: str):
    from code_erosion.cli import _is_test_path
    root = Path("/repo")
    assert _is_test_path(root / path, root)


@pytest.mark.parametrize("path", [
    "src/contest.py", "src/testing_tools.py", "src/spectral.py",
    "production-tests/report.py", "src/widget.ts", "vendor/widget.py",
])
def test_production_paths_are_not_classified_by_substring(path: str):
    from code_erosion.cli import _is_test_path
    root = Path("/repo")
    assert not _is_test_path(root / path, root)


def test_generated_and_vendor_trees_are_excluded(tmp_path: Path):
    for directory in ("vendor", "vendors", "generated", ".generated"):
        target = tmp_path / directory
        target.mkdir()
        (target / "module.py").write_text("def ignored():\n    return 1\n")
    (tmp_path / "src.py").write_text("def kept():\n    return 1\n")
    assert [path.name for path, _ in walk_files(tmp_path)] == ["src.py"]

# ------------------------------------------------------- trivial wrappers


def test_trivial_wrapper_detection_preserves_python_and_typescript_semantics(tmp_path: Path):
    from code_erosion.core import detect_trivial_wrappers

    cases = {
        "sample.py": '''def target(x):
    return x + 1

def documented(x):
    """A docstring does not count as an executable statement."""
    return target(x)

def not_wrapper(x):
    value = target(x)
    return value

alias = target
same = same
''',
        "sample.ts": '''function target(x) { return x + 1; }
function block(x) { return target(x); }
const expression = (x) => target(x);
const alias = target;
const memberAlias = api.target;
''',
    }
    parsed = []
    for name, source in cases.items():
        path = tmp_path / name
        path.write_text(source)
        spec = spec_for_suffix(path.suffix)
        tree = parse_source(source, spec)
        parsed.append((path, source, tree, spec, sloc_lines(source, tree, spec)))

    wrappers = detect_trivial_wrappers(parsed)
    observed = {(wrapper.file.name, wrapper.name) for wrapper in wrappers}
    assert observed == {
        ("sample.py", "target"),
        ("sample.py", "documented"),
        ("sample.py", "alias"),
        ("sample.ts", "target"),
        ("sample.ts", "block"),
        ("sample.ts", "alias"),
        ("sample.ts", "memberAlias"),
    }


def test_alias_wrapper_reports_declarator_lines_for_multiline_declarations(tmp_path: Path):
    from code_erosion.core import detect_trivial_wrappers

    source = (
        "function target(x: number): number { return x + 1; }\n"
        "const multiA = target,\n"
        "  multiB = api.target;\n"
    )
    path = tmp_path / "multi.ts"
    path.write_text(source)
    spec = spec_for_suffix(path.suffix)
    tree = parse_source(source, spec)
    parsed = [(path, source, tree, spec, sloc_lines(source, tree, spec))]

    wrappers = {w.name: (w.start_line, w.end_line) for w in detect_trivial_wrappers(parsed)}
    assert wrappers["multiA"] == (2, 2)
    assert wrappers["multiB"] == (3, 3)


def test_explicit_empty_known_function_names_disables_aliases(tmp_path: Path):
    from code_erosion.core import detect_trivial_wrappers

    source = "def target(x):\n    return x\n\nalias = target\n"
    path = tmp_path / "aliases.py"
    path.write_text(source)
    spec = spec_for_suffix(path.suffix)
    tree = parse_source(source, spec)
    parsed = [(path, source, tree, spec, sloc_lines(source, tree, spec))]

    wrappers = detect_trivial_wrappers(parsed, known_function_names=frozenset())
    assert [wrapper.name for wrapper in wrappers] == ["target"]
