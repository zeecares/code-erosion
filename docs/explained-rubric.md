# Explained code-quality rubric (ZEE-74)

Status: **design and first manual-scoring increment**, not a trained judge or a validated code-quality measure. The existing deterministic verbosity/erosion scanner and its production-only CI gate are unchanged. A book principle is a lens for review, not proof that an instance of code is defective. In particular, do not turn any rubric total into an autonomous merge gate or optimize code to lower it.

## First increment

`code_erosion.rubric` is a small, versioned library for *recording* reviewed findings. A reviewer supplies a category, severity (0-4), a concrete file/line span, observed evidence, an explanation of why it matters here, and a suggested check. The module validates the record and emits a stable JSON-ready finding with the category's book and principle. It does **not** read source, generate findings, use an LLM, or claim the severity is calibrated. A zero means a documented non-issue or positive control, not a flag; avoid recording speculative positives. `summarize` reports counts by category and severity, not a repo-level quality score. Existing scanner metrics remain separate signals.

The categories below are **provisional axial codes**. They are interpretations of principles, not quotations or page-precise claims. Citations identify books and chapter/topic anchors, not copied passages. A single finding may have competing interpretations. Cite the primary lens, retain the case evidence and a human decision; don't stack book names to manufacture authority.

| Code | Review question | Book anchor | Negative example / caution |
|---|---|---|---|
| `shallow_abstraction` | Is an interface adding more navigation than useful hidden complexity? | John Ousterhout, *A Philosophy of Software Design*, "Deep Modules" and "Information Hiding" | A small interface can still be deep; size alone is not evidence. |
| `mixed_responsibilities` | Does a function mix independent responsibilities or levels of abstraction? | Robert C. Martin, *Clean Code*, ch. 3 "Functions" ("Do One Thing", "One Level of Abstraction per Function") | A longer cohesive function is not automatically a violation. |
| `domain_language_drift` | Does implementation terminology disagree with the agreed model in this bounded context? | Eric Evans, *Domain-Driven Design*, "Ubiquitous Language" and "Bounded Context" | Identical words in different contexts need not mean identical things. |
| `unprotected_behavior_change` | Is a change to poorly understood existing behavior made without characterization evidence? | Michael Feathers, *Working Effectively with Legacy Code*, "Seams" and "Characterization Tests" | New, well-specified code need not be treated as legacy. |
| `knowledge_duplication` | Is one changing piece of knowledge represented in multiple places that can diverge? | Andrew Hunt & David Thomas, *The Pragmatic Programmer*, "The Evils of Duplication" (DRY) | Similar-looking code can encode distinct knowledge; deduplicating it can couple domains. |

Reading anchors: https://web.stanford.edu/~ouster/cgi-bin/book.php ; https://www.oreilly.com/library/view/clean-code-a/9780136083238/chapter03.xhtml ; https://www.domainlanguage.com/ddd/ ; https://www.pearson.com/en-us/subject-catalog/p/working-effectively-with-legacy-code/P200000008984/9780131177055 ; https://pragprog.com/titles/tpp20/the-pragmatic-programmer-20th-anniversary-edition/

## Research-to-loop plan (unbuilt)

1. Collect real review comments with code span, surrounding context, reviewer decision, and outcome. Open-code each individual complaint in the reviewer's words. Keep disagreements and non-issues, and strip sensitive code before any external model use.
2. Axial-code across examples: group repeated failure modes, distinguish cause from symptom, map a primary book lens and exceptions, and version changes to the taxonomy. Human reviewers settle contested codes. The five rows above are *starting hypotheses*, not categories inferred from a corpus.
3. On a separate held-out set, calibrate human agreement and judge explanations per module/change, not one repo-wide text dump. Return score, evidence span, reason, citation and uncertainty. Audit a random sample of both flagged and unflagged cases for misses, false positives, and drift. Track disagreement by language, domain and category.
4. Only with stable ground truth, test an optimization loop: a corpus-wide CASD-like pass can propose revised rules from historical findings; a GEPA-like search can propose prompts with fresh held-out rollouts and Pareto selection. Treat either as an experiment. Require behavior checks, human review, and an untouched holdout; never reward scanner-score reduction alone. Keep scanner movement informational and prefer sound design over metric geometry.

The present module only supports step 1's recording and a provisional taxonomy. There is no automatic judge, model call, validation result, or optimizer in this PR. A future capability should be a separate PR with a measured negative set and honest evaluation before any claim of usefulness.
