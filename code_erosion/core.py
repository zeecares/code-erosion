"""Core analysis: SLOC, clone detection, symbol extraction, CC, scoring.

Python semantics replicate scb-check 0.1.3 (SlopCodeBench reference).
TypeScript uses the same algorithms with the grammar mapping documented
in languages.py.
"""

from __future__ import annotations

import hashlib
import io
import math
import token as py_token
import tokenize
from dataclasses import dataclass, field
from itertools import groupby
from operator import attrgetter
from pathlib import Path

from tree_sitter import Node, Parser

from code_erosion.languages import LanguageSpec, get_language, get_tsx_language

HIGH_COMPLEXITY_THRESHOLD = 10
DEFAULT_EXCLUDED_DIRS = frozenset(
    {
        ".git", ".venv", "venv", "env", ".env", "__pycache__", "build",
        "dist", "node_modules", "site-packages", ".mypy_cache",
        ".pytest_cache", ".ruff_cache", ".tox", ".next", "coverage",
    }
)

MIN_CLONE_LINES = 3


# ---------------------------------------------------------------- walker


def walk_files(root: Path) -> list[tuple[Path, LanguageSpec]]:
    """Return (file, language) pairs under root, excluding noise dirs."""
    from code_erosion.languages import spec_for_suffix

    root = root.resolve()
    out: list[tuple[Path, LanguageSpec]] = []
    if root.is_file():
        spec = spec_for_suffix(root.suffix)
        return [(root, spec)] if spec else []

    stack = [root]
    while stack:
        current = stack.pop()
        for child in current.iterdir():
            if child.is_symlink():
                continue
            if child.is_dir():
                if child.name not in DEFAULT_EXCLUDED_DIRS:
                    stack.append(child)
                continue
            spec = spec_for_suffix(child.suffix)
            if spec is not None:
                out.append((child.resolve(), spec))
    return sorted(out, key=lambda item: item[0].as_posix())


# ---------------------------------------------------------------- parsing


class ParseError(ValueError):
    pass


def parse_source(source: str, spec: LanguageSpec, tsx: bool = False) -> "object":
    language = get_tsx_language() if tsx else get_language(spec)
    parser = Parser(language)
    tree = parser.parse(source.encode("utf-8"))
    if tree.root_node.has_error and spec.name == "typescript" and not tsx:
        # Retry with the TSX grammar for files using JSX syntax.
        return parse_source(source, spec, tsx=True)
    if tree.root_node.has_error:
        raise ParseError("tree-sitter reported a syntax error")
    return tree


def iter_nodes(node: Node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


# ---------------------------------------------------------------- SLOC


_PY_IGNORED_TOKENS = {
    py_token.COMMENT,
    py_token.DEDENT,
    py_token.ENDMARKER,
    py_token.INDENT,
    py_token.NEWLINE,
    py_token.NL,
}


def _python_sloc_lines(source: str, tree) -> frozenset[int]:
    """Lines containing executable tokens, minus line-owning docstrings."""
    lines: set[int] = set()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type not in _PY_IGNORED_TOKENS:
                lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        raise ParseError("tokenize failed")
    for start, end in _plain_string_ranges(tree.root_node, source, "python"):
        lines.difference_update(range(start, end + 1))
    return frozenset(lines)


def _ts_sloc_lines(source: str, tree) -> frozenset[int]:
    """Lines containing any non-comment token, minus line-owning strings."""
    lines: set[int] = set()
    for leaf in iter_nodes(tree.root_node):
        if leaf.child_count == 0 and leaf.type != "comment":
            lines.add(leaf.start_point[0] + 1)
    for start, end in _plain_string_ranges(tree.root_node, source, "typescript"):
        lines.difference_update(range(start, end + 1))
    return frozenset(lines)


def _string_prefix(text: str) -> str:
    prefix = []
    for char in text:
        if char in "rRbBuUfF":
            prefix.append(char.lower())
        else:
            break
    return "".join(prefix)


def _plain_string_ranges(root: Node, source: str, lang: str) -> list[tuple[int, int]]:
    """Line ranges of plain string expression statements owning their lines."""
    source_lines = source.splitlines()
    ranges: list[tuple[int, int]] = []
    for node in iter_nodes(root):
        if node.type != "expression_statement" or len(node.named_children) != 1:
            continue
        literal = node.named_children[0]
        if lang == "python":
            if literal.type != "string":
                continue
            text = literal.text.decode("utf-8") if literal.text else ""
            prefix = _string_prefix(text)
            if "b" in prefix or "f" in prefix:
                continue
        else:
            if literal.type != "string":
                continue
        if _owns_line(literal, source_lines):
            ranges.append((literal.start_point[0] + 1, literal.end_point[0] + 1))
    return ranges


def _owns_line(literal: Node, source_lines: list[str]) -> bool:
    start_row, start_col = literal.start_point
    end_row, end_col = literal.end_point
    start_line = source_lines[start_row] if start_row < len(source_lines) else ""
    end_line = source_lines[end_row] if end_row < len(source_lines) else ""
    return not start_line[:start_col].strip() and not end_line[end_col:].strip()


def sloc_lines(source: str, tree, spec: LanguageSpec) -> frozenset[int]:
    if spec.name == "python":
        return _python_sloc_lines(source, tree)
    return _ts_sloc_lines(source, tree)


def count_sloc_in_span(start_line: int, end_line: int, sloc: frozenset[int]) -> int:
    return sum(1 for line in sloc if start_line <= line <= end_line)


# ---------------------------------------------------------------- clones


@dataclass(frozen=True)
class CloneBlock:
    file: Path
    start_line: int
    end_line: int
    group_hash: str
    instance_count: int
    first_lines: tuple[str, ...]


def _is_type_checking_block(node: Node) -> bool:
    condition = node.child_by_field_name("condition")
    text = condition.text if condition is not None else None
    return text in {b"TYPE_CHECKING", b"typing.TYPE_CHECKING"}


def _normalize_subtree(node: Node, spec: LanguageSpec) -> str:
    variable_map: dict[str, str] = {}
    counter = 0

    def normalize(current: Node) -> str:
        nonlocal counter
        if current.type in spec.identifier_types:
            name = current.text.decode("utf-8") if current.text else ""
            if name not in variable_map:
                counter += 1
                variable_map[name] = f"$VAR{counter}"
            return variable_map[name]
        if current.type in spec.literal_tokens:
            return spec.literal_tokens[current.type]
        if current.type == "binary_expression" and spec.logical_operators:
            # Keep the operator visible so a&&b differs from a||b.
            operator = current.child_by_field_name("operator")
            op_text = operator.text.decode() if operator is not None else "?"
            children = [c for c in current.children if c.type != "comment"]
            inner = ",".join(normalize(c) for c in children)
            return f"{current.type}[{op_text}]({inner})"
        children = tuple(
            child
            for child in current.children
            if child.type != "comment" and not _is_plain_string_node(child, spec)
        )
        if not children:
            return current.type
        return f"{current.type}({','.join(normalize(c) for c in children)})"

    return normalize(node)


def _is_plain_string_node(node: Node, spec: LanguageSpec) -> bool:
    if node.type != "expression_statement" or len(node.named_children) != 1:
        return False
    literal = node.named_children[0]
    if literal.type != "string":
        return False
    if spec.name == "python":
        text = literal.text.decode("utf-8") if literal.text else ""
        prefix = _string_prefix(text)
        return "b" not in prefix and "f" not in prefix
    return True


def detect_clones(
    parsed_files: list[tuple[Path, str, object, LanguageSpec, frozenset[int]]],
    min_lines: int = MIN_CLONE_LINES,
) -> list[CloneBlock]:
    """Structural clone detection identical to scb-check's algorithm."""
    candidates = []
    for file_path, source, tree, spec, sloc in parsed_files:
        source_lines = source.splitlines()
        for node in iter_nodes(tree.root_node):
            if node.type not in spec.clone_node_types:
                continue
            if spec.name == "python" and _is_type_checking_block(node):
                continue
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            if count_sloc_in_span(start_line, end_line, sloc) < min_lines:
                continue
            digest = hashlib.md5(
                _normalize_subtree(node, spec).encode("utf-8"),
                usedforsecurity=False,
            ).hexdigest()[:12]
            candidates.append(
                (
                    digest,
                    file_path,
                    start_line,
                    end_line,
                    tuple(source_lines[start_line - 1 : min(end_line, start_line + 2)]),
                )
            )

    clones: list[CloneBlock] = []
    for digest, group_iter in groupby(
        sorted(candidates, key=lambda c: c[0]), key=lambda c: c[0]
    ):
        group = sorted(group_iter, key=lambda c: (c[1].as_posix(), c[2], c[3]))
        if len(group) < 2:
            continue
        for _, file_path, start_line, end_line, first_lines in group:
            clones.append(
                CloneBlock(
                    file=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    group_hash=digest,
                    instance_count=len(group),
                    first_lines=first_lines,
                )
            )
    return clones


# ---------------------------------------------------------------- symbols


@dataclass(frozen=True)
class FunctionSymbol:
    name: str
    file: Path
    start_line: int
    end_line: int
    sloc: int
    cc: int
    language: str

    @property
    def mass(self) -> float:
        return self.cc * math.sqrt(self.sloc)

    @property
    def is_high_cc(self) -> bool:
        return self.cc > HIGH_COMPLEXITY_THRESHOLD


def _symbol_name(node: Node, spec: LanguageSpec) -> str:
    name_node = node.child_by_field_name("name")
    if name_node is not None and name_node.text is not None:
        return name_node.text.decode("utf-8")
    return "<anonymous>"


def _function_cc(node: Node, spec: LanguageSpec) -> int:
    count = 0
    for current in iter_nodes(node):
        if current.type in spec.cc_node_types:
            count += 1
        elif (
            spec.logical_operators
            and current.type == "binary_expression"
            and (op := current.child_by_field_name("operator")) is not None
            and op.text is not None
            and op.text.decode() in spec.logical_operators
        ):
            count += 1
    return 1 + count


def extract_functions(
    file_path: Path,
    tree,
    sloc: frozenset[int],
    spec: LanguageSpec,
) -> list[FunctionSymbol]:
    """All function/method symbols, nested included (as scb-check does)."""
    symbols: list[FunctionSymbol] = []
    for node in iter_nodes(tree.root_node):
        if node.type not in spec.function_node_types:
            continue
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        symbols.append(
            FunctionSymbol(
                name=_symbol_name(node, spec),
                file=file_path,
                start_line=start_line,
                end_line=end_line,
                sloc=count_sloc_in_span(start_line, end_line, sloc),
                cc=_function_cc(node, spec),
                language=spec.name,
            )
        )
    return symbols


# ---------------------------------------------------------------- trivial wrappers


@dataclass(frozen=True)
class TrivialWrapper:
    file: Path
    name: str
    start_line: int
    end_line: int


def detect_trivial_wrappers(
    parsed_files: list[tuple[Path, str, object, LanguageSpec, frozenset[int]]],
    known_function_names: frozenset[str] | None = None,
) -> list[TrivialWrapper]:
    """Trivial wrappers per scb-check 0.1.3:

    - single-return functions: exactly one executable body statement
      (docstrings excluded) and it is a return_statement. Any return
      value counts - a delegating call is not required.
    - module-level aliases: `name = identifier_or_attribute` where the
      value resolves to a function defined in the scanned set.

    TypeScript port: single-return functions flagged identically;
    expression-bodied arrow functions are NOT flagged (idiomatic TS,
    documented in the README). Aliases use `const name = otherFn`.
    """
    wrappers: list[TrivialWrapper] = []
    if known_function_names is None:
        known = frozenset()
        for _p, _s, tree, spec, _sloc in parsed_files:
            for node in iter_nodes(tree.root_node):
                if node.type in spec.function_node_types:
                    known |= {_symbol_name(node, spec)}
        known_function_names = frozenset(known)

    for file_path, _source, tree, spec, _sloc in parsed_files:
        for node in iter_nodes(tree.root_node):
            if node.type in spec.function_node_types:
                if spec.name == "typescript" and node.type == "arrow_function":
                    body = node.child_by_field_name("body")
                    if body is not None and body.type != "statement_block":
                        continue  # expression-bodied arrow: idiomatic TS
                body = node.child_by_field_name("body")
                if body is None:
                    continue
                statements = [
                    s for s in body.named_children
                    if not _is_plain_string_node(s, spec)
                ]
                if len(statements) == 1 and statements[0].type == "return_statement":
                    wrappers.append(
                        TrivialWrapper(
                            file=file_path,
                            name=_symbol_name(node, spec),
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                        )
                    )
        # Module-level aliases.
        for statement in tree.root_node.named_children:
            target_text = value_text = None
            if statement.type in {"expression_statement"} and statement.named_children:
                assign = statement.named_children[0]
                if assign.type == "assignment" and len(assign.named_children) == 2:
                    target, value = assign.named_children
                    if target.type == "identifier" and value.type in {
                        "identifier", "attribute",
                    }:
                        target_text = target.text.decode() if target.text else None
                        value_text = value.text.decode() if value.text else None
            elif statement.type in {"lexical_declaration", "variable_declaration"}:
                for decl in statement.named_children:
                    if decl.type != "variable_declarator":
                        continue
                    tname = decl.child_by_field_name("name")
                    tval = decl.child_by_field_name("value")
                    if (
                        tname is not None and tval is not None
                        and tname.type == "identifier"
                        and tval.type in {"identifier", "member_expression"}
                        and tname.text and tval.text
                    ):
                        t, v = tname.text.decode(), tval.text.decode()
                        if v.split(".")[-1] in known_function_names and t != v:
                            wrappers.append(
                                TrivialWrapper(
                                    file=file_path,
                                    name=t,
                                    start_line=decl.start_point[0] + 1,
                                    end_line=decl.end_point[0] + 1,
                                )
                            )
            if (
                target_text and value_text
                and value_text.split(".")[-1] in known_function_names
                and target_text != value_text
            ):
                wrappers.append(
                    TrivialWrapper(
                        file=file_path,
                        name=target_text,
                        start_line=statement.start_point[0] + 1,
                        end_line=statement.end_point[0] + 1,
                    )
                )
    return wrappers


# ---------------------------------------------------------------- ast-grep


@dataclass(frozen=True)
class AstHit:
    file: Path
    line: int
    end_line: int
    rule_id: str
    message: str


# ---------------------------------------------------------------- scoring


@dataclass
class FileVerbosity:
    file: Path
    sloc: int
    clone_lines: int = 0
    ast_lines: int = 0
    wrapper_lines: int = 0
    union_lines: int = 0

    @property
    def verbosity(self) -> float:
        return self.union_lines / self.sloc if self.sloc else 0.0


@dataclass
class RepoReport:
    root: Path
    total_loc: int = 0
    files_scanned: int = 0
    verbosity: float = 0.0
    erosion: float = 0.0
    clone_loc: int = 0
    ast_grep_flagged_loc: int = 0
    trivial_wrapper_loc: int = 0
    verbosity_flagged_loc: int = 0
    total_functions: int = 0
    high_cc_functions: int = 0
    total_mass: float = 0.0
    high_cc_mass: float = 0.0
    per_file: list[FileVerbosity] = field(default_factory=list)
    functions: list[FunctionSymbol] = field(default_factory=list)
    clones: list[CloneBlock] = field(default_factory=list)
    ast_hits: list[AstHit] = field(default_factory=list)
    wrappers: list[TrivialWrapper] = field(default_factory=list)
    parse_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_report(
    root: Path,
    parsed: list[tuple[Path, str, object, LanguageSpec, frozenset[int]]],
    clones: list[CloneBlock],
    ast_hits: list[AstHit],
    wrappers: list[TrivialWrapper],
    functions: list[FunctionSymbol],
    parse_failures: list[str],
    warnings: list[str],
) -> RepoReport:
    report = RepoReport(root=root)
    report.parse_failures = parse_failures
    report.warnings = warnings
    report.files_scanned = len(parsed)
    report.functions = sorted(
        functions, key=lambda f: (f.file.as_posix(), f.start_line, f.name)
    )
    report.clones = clones
    report.ast_hits = ast_hits
    report.wrappers = wrappers

    sloc_by_file = {path: sloc for path, _s, _t, _spec, sloc in parsed}

    def span_lines(file: Path, start: int, end: int) -> frozenset[int]:
        sloc = sloc_by_file.get(file, frozenset())
        return frozenset(l for l in range(start, end + 1) if l in sloc)

    clone_lines_by_file: dict[Path, set[int]] = {}
    for clone in clones:
        clone_lines_by_file.setdefault(clone.file, set()).update(
            span_lines(clone.file, clone.start_line, clone.end_line)
        )
    ast_lines_by_file: dict[Path, set[int]] = {}
    for hit in ast_hits:
        ast_lines_by_file.setdefault(hit.file, set()).update(
            span_lines(hit.file, hit.line, hit.end_line)
        )
    wrapper_lines_by_file: dict[Path, set[int]] = {}
    for wrapper in wrappers:
        wrapper_lines_by_file.setdefault(wrapper.file, set()).update(
            span_lines(wrapper.file, wrapper.start_line, wrapper.end_line)
        )

    all_files = (
        set(sloc_by_file)
        | set(clone_lines_by_file)
        | set(ast_lines_by_file)
        | set(wrapper_lines_by_file)
    )
    for file in sorted(all_files, key=lambda p: p.as_posix()):
        sloc = len(sloc_by_file.get(file, frozenset()))
        clone_l = clone_lines_by_file.get(file, set())
        ast_l = ast_lines_by_file.get(file, set())
        wrap_l = wrapper_lines_by_file.get(file, set())
        union = clone_l | ast_l | wrap_l
        report.per_file.append(
            FileVerbosity(
                file=file,
                sloc=sloc,
                clone_lines=len(clone_l),
                ast_lines=len(ast_l),
                wrapper_lines=len(wrap_l),
                union_lines=len(union),
            )
        )
        report.total_loc += sloc
        report.clone_loc += len(clone_l)
        report.ast_grep_flagged_loc += len(ast_l)
        report.trivial_wrapper_loc += len(wrap_l)
        report.verbosity_flagged_loc += len(union)

    report.verbosity = (
        report.verbosity_flagged_loc / report.total_loc if report.total_loc else 0.0
    )
    report.total_functions = len(report.functions)
    report.total_mass = sum(f.mass for f in report.functions)
    report.high_cc_mass = sum(f.mass for f in report.functions if f.is_high_cc)
    report.high_cc_functions = sum(1 for f in report.functions if f.is_high_cc)
    report.erosion = (
        report.high_cc_mass / report.total_mass if report.total_mass else 0.0
    )
    return report
