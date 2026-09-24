# Trust

The short version: the metric machinery is tested and self-gating; the human-vs-agent calibration is not a classifier, and the refactoring loop has only ever run deterministic stand-ins. Trust it as a measurement tool with guardrails, not as an autonomous refactorer.

## Proven

- 87 tests green (v0.7.0); CI runs pytest on Python 3.10 and 3.12.
- The scanner gates itself: every PR and push runs the action from the same checkout in gate mode against production erosion (regression threshold 0.01). The repo is gated by the scanner version it ships.
- The Python engine replicates the reference implementation (scb-check 0.1.3) semantics, including its vendored 197-rule ast-grep set.
- Loud failure: missing or unreadable rules, a missing or shadowed ast-grep binary, or ast-grep errors exit with code 2. No partial scores.
- Production and test corpora are scored separately; the gate reads production only, so added tests cannot dilute it. Old baselines fail loudly with migration guidance.
- Loop guardrails: serialized runs, at most one open loop PR, candidates touching the committed baseline are rejected, the repo's own checks must pass, production erosion must improve, and the outcome is a draft PR or a logged discard. A human merges.

## Not proven

- The human-vs-agent bands as a classifier. On real agent-written code, erosion lands in the agent band but verbosity does not separate from the human band. TypeScript bands are an extrapolation, with a known React false positive.
- The refactoring loop with a real model executor. The accepted and discarded paths are proven with deterministic stand-ins, labeled as such; the scheduled run uses the stand-in by default.
- Markdown and TOML are unsupported.
- The score is gameable once it becomes a target; treat single-score improvements with suspicion.

## Invariants

These must never break. A change that breaks one is a bug, regardless of intent.

1. The scanner never modifies scanned source. It writes only to explicit output paths (`--write-baseline`, `--suggestions-out`, `--comment-out`, history and badge files).
2. No partial scores: any missing capability fails loudly with exit code 2.
3. The gate reads the production corpus only.
4. The loop never auto-merges: draft PRs only, one at a time, baseline untouchable, checks green, erosion improved, every outcome appended to durable loop memory.
5. No model calls unless an executor is explicitly configured by the calling repo; the default is the deterministic stand-in.

## Measurement

Scoring is fully deterministic; no LLM judge anywhere in the metric path. The committed trend history (`.code-erosion-history.jsonl`) measures drift over time, and every loop candidate is judged by a before/after scan pair plus the repository's own checks.

## Removal

1. `pip uninstall code-erosion`
2. Delete `.code-erosion.json`, `.code-erosion-history.jsonl`, and any badge file from repos that used the action.
3. Delete the workflow files that reference the action.
4. Close any open `code-erosion-loop/*` pull request, delete the `code-erosion-loop-memory` branch, and remove any executor variables and API-key secrets you added for it.

Verify removal: no workflow runs or PR comments from the action after removal, and no `code-erosion-loop*` branches remain.
