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

Install from GitHub, or clone and install editable:

```bash
pip install "git+https://github.com/zeecares/code-erosion"

pip install -e .            # from a clone; pulls tree-sitter grammars + the ast-grep CLI (sg)
python -m code_erosion /path/to/repo
python -m code_erosion /path/to/repo --verbose   # per-file verbosity + per-function mass tables
python -m code_erosion /path/to/repo --json      # machine-readable report
```

Scans `.py` and `.ts/.tsx/.js/.jsx/.mts/.cts/.mjs/.cjs` files, skipping
`.git`, `node_modules`, build output, caches, and virtualenvs.

### Bundled rules and loud failure

The ast-grep rule sets ship as package data under `code_erosion/rules/` and
are resolved through `importlib.resources`, so a `pip install`ed copy (wheel
or source) finds them inside the installed package. A scan that cannot load
or execute its rule set exits non-zero with an explicit error instead of
emitting a plausible-looking partial score - that covers missing, empty, or
unreadable rule files, a missing or shadowed ast-grep binary (`sg` is
verified to really be ast-grep, since the name collides with the Unix
setgroups utility), and ast-grep itself erroring out. If you see that error, reinstall code-erosion; do not trust a score
produced without the full rule set.

Migration note (v0.6.1): the rules moved from the repository-root `rules/`
directory into the package (`code_erosion/rules/`), and the TypeScript tree
was renamed `ts` -> `typescript` to match the language key the scanner
dispatches on. Any external tooling that referenced `rules/ts` directly
should point at `code_erosion/rules/typescript`. v0.6.0 and earlier wheels
installed the rules to `<prefix>/rules/`, a location the runtime never
looked in, so installed scans silently skipped every ast-grep rule.

## v0.7 migration: production is the gate

v0.7 reports three explicit views: **production**, **test/spec**, and the legacy **combined** whole-repository score. The CI gate and refactoring loop now evaluate production erosion only. Test/spec and combined metrics remain visible diagnostics, so adding low-erosion tests cannot dilute the gate.

Corpus classification is intentionally conservative. A file is test/spec code when any directory component is exactly `test`, `tests`, `spec`, `specs`, or `__tests__`; Python also recognizes root or nested `test_*.py` and `*_test.py`, while JavaScript/TypeScript recognize `*.test.*` and `*.spec.*`. Broad substrings such as `contest` and `testing_tools` do not match. Dependency, build, generated, and vendor trees are excluded from all corpora. Unusual test layouts should use one of these conventional path shapes; otherwise they remain production because a false test classification would weaken the gate.

On code-erosion's own v0.7 scan, the legacy combined score is 0.523 while the new production score is 0.629 and test/spec is 0.260. This is expected movement from separating the low-erosion test denominator, not a source refactor.

The baseline schema is now 2. Existing schema-1 baselines fail loudly rather than silently falling back to the dilutable whole-repo score. Regenerate once with `code-erosion . --write-baseline .code-erosion.json`, review the production/test split, and commit the movement with the v0.7 upgrade. The combined score remains under the top-level `erosion` and `verbosity` JSON keys for consumers that use it as a diagnostic; new per-corpus reports are under `corpora.production` and `corpora.test`.

## Language support

| Language | Files | Parsing | CC + drivers | Clones | Wrappers | ast-grep rules |
|---|---|---|---|---|---|---|
| Python | `.py` | tree-sitter-python + stdlib `tokenize` (scb-check 0.1.3 semantics) | yes | yes | yes | 197 vendored rules (`code_erosion/rules/python/`) |
| TypeScript | `.ts`, `.tsx`, `.mts`, `.cts` | tree-sitter-typescript (adapted rule set) | yes | yes | yes | 9 analog rules (`code_erosion/rules/typescript/`) |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` | same TypeScript engine, with a TSX-grammar retry so JSX inside `.js` also parses | yes | yes | yes | 9 analog rules (`code_erosion/rules/typescript/`) |

Language semantics worth knowing:

- Anonymous JS/TS callables are named from their binding for the worklist:
  `const f = () => ...`, `obj.m = function ...`, `exports.f = () => ...`,
  object-literal keys, and `export default`. Genuine callbacks stay
  `<anonymous>`. (v0.5)
- Python `and`/`or` drivers are reported as `logical_and`/`logical_or`,
  the same kinds TypeScript emits, with hints for the Python-only kinds
  (assertions, comprehensions, comprehension filters). (v0.5)
- Python lambdas are not callables in the grammar mapping, matching
  scb-check: a lambda's conditional expression attributes to the enclosing
  function.
- Known TS inflation source, unchanged: the single-return wrapper rule
  flags small React function components (idiomatic TSX); expression-bodied
  arrows are excluded.


## CI gate (GitHub Action)

This repo is itself a composite GitHub Action. Add it to any repository you
want monitored:

```yaml
# .github/workflows/code-erosion.yml
name: code-erosion
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
  pull-requests: write   # to post the report comment
jobs:
  erosion:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: zeecares/code-erosion@main   # pin to a tag or SHA for production
        with:
          mode: gate          # 'informational' (default) never fails; 'gate' enforces
          threshold: "0.01"   # allowed absolute erosion regression
```

What it does on each PR:

1. Scans the checkout (same engine as the CLI).
2. Diffs against the committed baseline (default `.code-erosion.json`):
   repo-level verbosity/erosion deltas, new and resolved high-complexity
   functions, and the biggest per-function erosion-mass movers.
3. Posts or updates one PR comment with the delta (the report also lands in
   the job summary, so a token without `pull-requests: write` only loses the
   comment, not the gate).
4. In `mode: gate`, fails the check when erosion regresses beyond the
   threshold. In `mode: informational` (the default) it only reports.

**Bootstrap.** With no baseline committed, the action generates a seed at the
baseline path and tells you to commit it; nothing fails. You can also generate
one locally:

```bash
code-erosion . --write-baseline .code-erosion.json
```

**Intentional movement.** When a PR legitimately changes the score (a refactor,
a big generated file), regenerate the baseline and commit it in the same PR:
the gate passes and the reviewer sees the new baseline in the diff, with the
comment still showing the movement against the old one. A silent baseline bump
is visible in the PR diff - that is the review hook, by design.

**Honest limits.** The score is a tripwire, not a target - the source post
warns any single metric dies once optimized for (Goodhart), so the gate watches
*movement*, it does not grade the codebase. Reference bands are
Python-calibrated; TypeScript scores come from an adapted rule set and are
directionally right, not lab-grade. The repo is public: use `zeecares/code-erosion@main`
from any repository, and pin to a tag or SHA for production.

### Trend history and README badge (R4)

The gate tells you a PR moved the score; the trend tells you where the repo is
going. With the `history` input at its default (`.code-erosion-history.jsonl`):

1. On every push to the default branch, the action appends one JSON record
   (date, commit SHA, verbosity, erosion, SLOC, high-CC function count) to the
   history file and commits it back (`[skip ci]`, so no workflow loop).
2. On PRs, when the committed history exists, the report comment gains a trend
   section: direction (improving / worsening / flat) and the last few
   default-branch scans.
3. The file is plain JSONL, one scan per line - chart it with anything.

Committing the history needs more than the minimal permissions above:

```yaml
permissions:
  contents: write      # to commit the history file back on push
  pull-requests: write # to post the report comment
```

Two honest limits: if the workflow keeps `contents: read`, or branch
protection blocks the Actions app from pushing to the default branch, the
history step records in the workspace and posts a warning instead of failing -
the scan and gate are unaffected. Set `history: ""` to turn the feature off
entirely.

For a README badge, opt in with `badge: ".code-erosion-badge.json"`; the action
rewrites it on each push with a shields.io endpoint payload - the score plus a
color from the reference bands (green at/below the human band, red past the
agent band). Embed it with:

```md
![code erosion](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/<owner>/<repo>/main/.code-erosion-badge.json)
```

## Method fidelity

The Python engine replicates **scb-check 0.1.3** (the reference implementation
used by SlopCodeBench, github.com/SprocketLab/slop-code-bench), including its
bundled 197-rule ast-grep set (vendored under `code_erosion/rules/python/`, Apache-2.0,
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
  analog rules (`code_erosion/rules/typescript/`) vs 197 Python rules. It systematically
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

### Self-dogfood (v0.5)

This repository scans and gates itself: `.github/workflows/code-erosion.yml`
runs its own action (`uses: ./`, gate mode) on every PR against the committed
baseline `.code-erosion.json`, and the test suite runs in
`.github/workflows/tests.yml`.

| Repo (commit) | Lang | SLOC | Verbosity | Erosion | Reading |
|---|---|---|---|---|---|
| zeecares/code-erosion (v0.5.0) | Py | 1,841 | **0.117** | **0.543** | Verbosity below the human band; erosion between the bands, agent-leaning |

Honest read: the scanner's own worst offender is `detect_trivial_wrappers`
(CC 41, 14% of total mass) - the wrapper detector is itself the most
branch-heavy function in the repo, with `main` (CC 17) and
`diff_against_baseline` (CC 22) behind it. They are on the refactoring
worklist, tracked as follow-up work rather than silently baselined away.

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

MIT (this repo). Vendored `code_erosion/rules/python/*.yaml` are from scb-check 0.1.3,
Apache-2.0, copyright the SlopCodeBench authors.

## Agent-ready refactoring suggestions (v0.4)

The scanner can now turn its high-CC ranking into a refactoring worklist with
source anchors, the branches and conditions driving complexity, per-function
deep-module guidance, and an explicit verification contract:

```bash
code-erosion . \
  --suggestions-out code-erosion-suggestions.json \
  --agent-instructions-out code-erosion-agent-instructions.md
```

The JSON is intended for orchestration. The Markdown file is a ready-to-paste
prompt for Claude Code, OpenClaw, or another coding agent: inspect callers and
tests, find a responsibility seam, add characterization or differential tests,
refactor without thin wrappers, re-run the repository checks and scanner, then
open a PR with behavior proof and before/after numbers.

The GitHub Action does this with zero configuration. Every run uploads both
files as the `code-erosion-suggestions` artifact and adds the ranked suggestions
to the job summary and PR comment. Gate behavior is unchanged.

This closes the handoff from measurement to execution instructions, not the
optimization loop. It does not invoke an agent, review the resulting patch, or
learn which instructions work. R7/GEPA supplies that outer candidate -> rollout
-> held-out evaluation -> prompt-update loop. Keep tests as a hard gate and use
held-out human judgment, since erosion is gameable and TypeScript reference
bands remain extrapolated from Python.

## Self-driving refactoring loop (experimental)

`.github/workflows/code-erosion-loop.yml` is the first guarded outer loop. It
scans the repository, selects the top suggestion, runs one executor, discovers
and runs the repository's test/typecheck/lint commands, measures the candidate,
and opens an accepted patch as a **draft** PR. A candidate is discarded when
checks fail, erosion does not improve, the committed baseline changes, or a test
file loses more lines than it gains. Only one `code-erosion-loop/*` PR may be
open at once.

Every iteration, accepted or discarded, is appended as JSONL on the dedicated
`code-erosion-loop-memory` branch. This keeps outcome memory out of `main` while
leaving R8/GEPA a durable training trail: offender, executor, decision/reasons,
before/after scores, changed files, and check results. GEPA is deliberately not
part of this release.

### Bring your own executor

The loop has no model or agent dependency. Its executor contract is:

- input: `CODE_EROSION_PROMPT` (agent-instructions Markdown),
  `CODE_EROSION_SUGGESTIONS` (full JSON), and `CODE_EROSION_TARGET` (rank-1 JSON)
- working directory: the checked-out repository
- output: an uncommitted patch in that working directory
- exit: zero when execution completed; the loop, not the executor, discovers
  and runs checks, rescans, judges, records memory, and opens any draft PR

The default `standin` executor is deterministic and intentionally makes no
source refactor. It proves scan, selection, verification, rejection, artifacts,
and memory without pretending an agent ran.

For live mode, define trusted repository variables (Settings -> Secrets and
variables -> Actions -> Variables):

- `CODE_EROSION_EXECUTOR_INSTALL`: optional install command
- `CODE_EROSION_EXECUTOR_COMMAND`: required headless command; read the prompt
  from `$CODE_EROSION_PROMPT` and edit the checkout in place

Then dispatch **code-erosion self-driving loop** with `executor=command`.
Repository variables are trusted configuration; PR contents cannot select the
command. Provider credentials remain ordinary repository secrets. Add only the
secret for the executor you chose.

Claude Code example (secret `ANTHROPIC_API_KEY`):

```text
CODE_EROSION_EXECUTOR_INSTALL=npm install -g @anthropic-ai/claude-code
CODE_EROSION_EXECUTOR_COMMAND=claude -p "$(cat "$CODE_EROSION_PROMPT")" --allowedTools "Read,Edit,Write,Bash"
```

Codex example (secret `OPENAI_API_KEY`):

```text
CODE_EROSION_EXECUTOR_INSTALL=npm install -g @openai/codex
CODE_EROSION_EXECUTOR_COMMAND=codex exec --ephemeral - < "$CODE_EROSION_PROMPT"
```

Any other local or hosted agent CLI fits the same shell contract. For example,
a wrapper script can read the three paths, call its provider, and apply a patch
with `git apply`. No executor may declare its own candidate accepted.

This is autonomous candidate production, not autonomous acceptance: a human
still reviews and merges the draft. The guard against weakened tests is
intentionally conservative, not a proof of semantic test quality.

### Reuse the guarded candidate engine

The orchestration core is a composite action at `loop/action.yml`. Another repo
can run one candidate without copying scanner or judge logic:

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }
- name: Install this repo's dependencies first
  run: npm ci # example; the candidate engine discovers checks, it does not guess setup
- id: candidate
  uses: zeecares/code-erosion/loop@main # pin a release/SHA in production
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }} # only if the chosen CLI needs it
  with:
    path: .
    executor: command
    executor-install: ${{ vars.CODE_EROSION_EXECUTOR_INSTALL }}
    executor-command: ${{ vars.CODE_EROSION_EXECUTOR_COMMAND }}
- if: steps.candidate.outputs.disposition == 'open_draft_pr'
  uses: peter-evans/create-pull-request@v7
  with:
    draft: true
    branch: code-erosion-loop/${{ github.run_id }}
    body-path: ${{ steps.candidate.outputs.outcome }}
```

The action returns `accepted`, `disposition` (`open_draft_pr` or `discard`), and
the outcome JSON path. The checked-in wrapper adds serialized scheduling,
one-open-loop-PR enforcement, durable memory, artifact upload, and draft-PR
creation. Consumers can use that wrapper as a reference while keeping their own
schedule, credentials, and branch policy.

The test suite includes two deterministic end-to-end proofs. The no-op executor
is discarded. The accepted fixture refactors an eleven-branch lookup into a
data table, runs behavior tests covering all cases and fallback, demonstrates a
strict erosion reduction, returns `open_draft_pr`, and pins the wrapper's
`draft: true` handoff. This proves the acceptance plumbing without claiming that
a deterministic fixture is a model run.
