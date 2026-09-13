# code-erosion

Measures how sloppy a codebase is, using the two SlopCodeBench metrics from
[Measuring the sloppiness of code](https://earendil.com/posts/measuring-code-sloppiness/)
(Earendil, 2026-09-10):

- **Verbosity** = |AST-grep flagged lines UNION clone lines UNION trivial-wrapper lines| / SLOC
- **Erosion**: `mass(f) = CC(f) * sqrt(SLOC(f))`; Erosion is the fraction of total mass
  held by functions with cyclomatic complexity CC > 10.

Reference bands from the post (human repos vs agent-generated code):

| Metric    | Human repos   | Agent code    |
|-----------|---------------|---------------|
| Verbosity | 0.15 +/- 0.06 | 0.33 +/- 0.10 |
| Erosion   | 0.31 +/- 0.17 | 0.68 +/- 0.20 |

## Usage

```bash
pip install -e .            # pulls tree-sitter grammars + the ast-grep CLI (sg)
python -m code_erosion /path/to/repo
python -m code_erosion /path/to/repo --verbose   # per-file verbosity + per-function mass tables
python -m code_erosion /path/to/repo --json      # machine-readable report
```

Scans `.py` and `.ts/.tsx/.js/.jsx/.mts/.cts/.mjs/.cjs` files, skipping
`.git`, `node_modules`, build output, caches, and virtualenvs.

## Method fidelity

The Python engine replicates **scb-check 0.1.3** (the reference implementation
used by SlopCodeBench, github.com/SprocketLab/slop-code-bench), including its
bundled 197-rule ast-grep set (vendored under `rules/python/`, Apache-2.0,
copyright the SlopCodeBench authors):

- SLOC: lines with executable tokens (stdlib `tokenize`), minus comment-only,
  blank, and line-owning docstring lines.
- Clones: tree-sitter subtrees of statement-level nodes (functions, if/for/
  while/with/try/match) with >= 3 SLOC lines, hashed after normalizing
  identifiers to `$VARn` and literals to `$STR/$INT/...`; groups with >= 2
  instances contribute every instance's SLOC lines.
- AST-grep flags: `sg scan` with the vendored rules; hit line ranges
  intersected with SLOC lines.
- Trivial wrappers: single-return functions (docstrings excluded) plus
  module-level `name = other_function` aliases.
- CC: 1 + count of decision nodes (if/elif/for/while/except/assert/
  comprehensions/generators/boolean operators/conditional expressions/
  comprehension if-clauses) across the whole function subtree.

Validation vs `scb-check==0.1.3` (same repos, `--report --include-all`):

| Repo | Metric | scb-check | code-erosion |
|------|--------|-----------|--------------|
| fly-brain-lab | verbosity | 0.0754 | 0.0754 (identical) |
| fly-brain-lab | erosion | 0.5164 | 0.5164 (identical) |
| slop-code-bench/src | verbosity | 0.2434 | 0.2432 |
| slop-code-bench/src | erosion | 0.5016 | 0.4993 |

The small slop-code-bench deltas come from one documented interpretation
choice: scb-check's visitor only descends through module/class/decorated/block
children, missing functions nested inside module-level `if`/`try` statements;
we count every function node (1432 vs 1416 functions). ast-grep version used
here is 0.45.3 vs scb-check's pinned 0.42.1; flagged LOC matched exactly on
both validation repos.

## TypeScript adaptation (interpretation choices)

SlopCodeBench/scb-check are Python-only. The TypeScript port applies the same
algorithms with grammar-mapped node types:

- CC decision points: `catch_clause` for `except_clause`, `ternary_expression`
  for `conditional_expression`, `do_statement`, each non-default
  `switch_case`, and `&&`/`||`/`??` operators per occurrence.
- SLOC: lines containing any non-comment token (punctuation included, matching
  Python's token-based counting), minus line-owning plain string statements.
- Clone candidates add `method_definition`, `for_in_statement`,
  `switch_statement`; identifiers, property identifiers, and shorthand
  property identifiers all normalize to `$VARn`.
- The AST-grep component is a **partial port**: 9 hand-authored TypeScript
  analog rules (`rules/ts/`) vs 197 Python rules. It systematically
  under-measures the rule component relative to Python, so cross-language
  verbosity comparisons should read the components (`clone_loc`,
  `ast_grep_flagged_loc`, `trivial_wrapper_loc`) separately.
- Known inflation source: the single-return wrapper rule flags small React
  function components (idiomatic TSX), so TSX-heavy UI files score high on the
  wrapper component. Expression-bodied arrows are excluded.

## Results (2026-09-13)

| Repo (commit) | Lang | SLOC | Verbosity | Erosion | Reading |
|---|---|---|---|---|---|
| zeecares/4allhuman (01f5863) | TS+Py | 5,785 | **0.084** | **0.683** | Verbosity below the human band; erosion inside the agent band |
| zeecares/fly-brain-lab (main) | Py | 1,140 | **0.075** | **0.516** | Verbosity below the human band; erosion between the bands, agent-leaning |

Reproduce with `python -m code_erosion <clone> --json` (full per-function/per-file detail).

### 4allhuman top-5 erosion offenders (refactoring worklist)

| Function | Location | SLOC | CC | Mass |
|---|---|---|---|---|
| Home | src/app/page.tsx:327 | 1079 | 120 | 3941.8 |
| parseTermsTxt | src/lib/terms.ts:67 | 72 | 33 | 280.0 |
| render_markdown | wiki-viewer/serve.py:66 | 59 | 33 | 253.5 |
| CloudflareCheckCard | src/app/page.tsx:210 | 103 | 19 | 192.8 |
| POST | src/app/api/verify/route.ts:33 | 75 | 19 | 164.5 |

`Home` alone holds 46% of the repo's total erosion mass.

## Caveats

- The reference bands are calibrated on Python repos and Python-track agent
  output; applying them to TypeScript code is an extrapolation.
- The metrics are structural; they say nothing about correctness or taste
  beyond what the rule set encodes.
- `--include-all` semantics: scb-check's `scbc ignore` directives and
  per-rule count thresholds are not implemented (none exist in the scanned
  repos).

## License

MIT (this repo). Vendored `rules/python/*.yaml` are from scb-check 0.1.3,
Apache-2.0, copyright the SlopCodeBench authors.
