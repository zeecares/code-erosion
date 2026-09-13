"""Per-language grammar configuration for the erosion/verbosity engines."""

from __future__ import annotations

from dataclasses import dataclass, field

import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language


@dataclass(frozen=True)
class LanguageSpec:
    name: str
    suffixes: tuple[str, ...]
    # Statement-level nodes eligible as clone candidates (scb-check set,
    # with direct structural analogs for TypeScript).
    clone_node_types: frozenset[str]
    # Decision-point node types for cyclomatic complexity (CC = 1 + count).
    cc_node_types: frozenset[str]
    # Tree-sitter node types that hold callable bodies.
    function_node_types: frozenset[str]
    # Literal node normalization for clone hashing.
    literal_tokens: dict[str, str] = field(default_factory=dict)
    # Node types treated as renameable identifiers in clone hashing.
    identifier_types: frozenset[str] = frozenset({"identifier"})
    # Logical operators counted per occurrence for CC.
    logical_operators: frozenset[str] = frozenset()


PYTHON = LanguageSpec(
    name="python",
    suffixes=(".py",),
    clone_node_types=frozenset(
        {
            "function_definition",
            "if_statement",
            "for_statement",
            "while_statement",
            "with_statement",
            "try_statement",
            "match_statement",
        }
    ),
    cc_node_types=frozenset(
        {
            "if_statement",
            "elif_clause",
            "for_statement",
            "while_statement",
            "except_clause",
            "assert_statement",
            "list_comprehension",
            "set_comprehension",
            "dictionary_comprehension",
            "generator_expression",
            "boolean_operator",
            "conditional_expression",
            "if_clause",
        }
    ),
    function_node_types=frozenset({"function_definition"}),
    literal_tokens={
        "string": "$STR",
        "string_content": "$STR",
        "f_string": "$STR",
        "string_fragment": "$STR",
        "bytes": "$STR",
        "integer": "$INT",
        "float": "$FLOAT",
        "imaginary": "$FLOAT",
        "true": "$BOOL",
        "false": "$BOOL",
        "none": "$NONE",
    },
)

# TypeScript adaptation. Interpretation choices vs the Python reference:
# - `catch_clause` stands in for `except_clause`, `ternary_expression` for
#   `conditional_expression`, `do_statement` for the do-while loop Python
#   lacks, and each non-default `switch_case` counts as one decision point
#   (a switch case is structurally an if/elif rung).
# - Logical && / || appear as binary_expression nodes whose operator child
#   is counted per occurrence, mirroring Python boolean_operator counting.
# - `property_identifier` is normalized like `identifier` for clone
#   hashing, matching how Python attribute names normalize.
TYPESCRIPT = LanguageSpec(
    name="typescript",
    suffixes=(".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs"),
    clone_node_types=frozenset(
        {
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
            "if_statement",
            "for_statement",
            "for_in_statement",
            "while_statement",
            "do_statement",
            "with_statement",
            "try_statement",
            "switch_statement",
        }
    ),
    cc_node_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "for_in_statement",
            "while_statement",
            "do_statement",
            "catch_clause",
            "ternary_expression",
            "switch_case",
        }
    ),
    function_node_types=frozenset(
        {
            "function_declaration",
            "generator_function_declaration",
            "function_expression",
            "arrow_function",
            "method_definition",
        }
    ),
    literal_tokens={
        "string": "$STR",
        "template_string": "$STR",
        "string_fragment": "$STR",
        "number": "$INT",
        "true": "$BOOL",
        "false": "$BOOL",
        "null": "$NONE",
        "undefined": "$NONE",
        "regex": "$STR",
    },
    identifier_types=frozenset({"identifier", "property_identifier", "shorthand_property_identifier"}),
    logical_operators=frozenset({"&&", "||", "??"}),
)

LANGUAGES = {spec.name: spec for spec in (PYTHON, TYPESCRIPT)}

_SUFFIX_TO_SPEC = {
    suffix: spec for spec in LANGUAGES.values() for suffix in spec.suffixes
}


def spec_for_suffix(suffix: str) -> LanguageSpec | None:
    return _SUFFIX_TO_SPEC.get(suffix)


def get_language(spec: LanguageSpec) -> Language:
    if spec.name == "python":
        return Language(tree_sitter_python.language())
    return Language(tree_sitter_typescript.language_typescript())


def get_tsx_language() -> Language:
    return Language(tree_sitter_typescript.language_tsx())
