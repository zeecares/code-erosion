# Architecture guide

This is the intended module tree, not a rule to split code by line count. Read [TRUST.md](TRUST.md) for invariants and [docs/explained-rubric.md](docs/explained-rubric.md) for the proposed human/LLM review layer.

```
code_erosion/
  core.py, languages.py, rules/   parsing, SLOC, AST rules and deterministic measurements
  cli.py                         command-line boundary, composition and IO
  baseline.py, history.py        persistent measurement records
  suggestions.py                ranked worklist and refactoring instructions
  loop.py                       candidate/check/rescan policy, never auto-merge
  rubric.py                     manually explained findings and book lenses; no model calls
```

## Design rule: deep modules

Prefer a small, stable interface that hides a substantial cohesive decision, following Ousterhout's *A Philosophy of Software Design* ("Deep Modules", "Information Hiding"). Put parser specifics behind language/scanner interfaces and keep orchestration out of them. Keep measured facts in the scanner, policy in baseline/loop, presentation in CLI/suggestions, and human judgment in rubric. Dependency direction is from orchestration/presentation to the measurement core, not back from core to CLI or model code. A rubric finding can reference measured evidence, but must not alter deterministic scan output or gate behavior.

Do not make a file per tiny helper, add pass-through wrappers, or extract functions solely to lower erosion. Name the responsibility boundary and what complexity it hides before changing the tree. A module with many lines can be deep; a short wrapper can be shallow. Pin behavior with tests before restructuring. If a new capability has no natural owner, write down its responsibility and dependencies in the PR before adding a module.

Proposed future layers (not implemented): review corpus/open coding -> axial taxonomy and human calibration -> per-module explanatory judge -> random audit -> guarded optimization experiment. The optimizer does not write into the metric path. Never gate or merge on an unvalidated model judgment or a single gameable metric.
