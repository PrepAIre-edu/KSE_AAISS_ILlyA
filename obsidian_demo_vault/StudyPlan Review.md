---
reviewed: 2026-08-22
source_note: "[[StudyPlan]]"
---

# Study Plan Review

## ECTS / Budget
- Target courses (ML, PROB) alone: **10 ECTS** total, against the 20 ECTS budget → within budget (`validate_study_plan`: `over_budget: false`).
- However, the actual **term 1 plan** (LINALG + ML + PROB) totals **13 ECTS**, against a 12 ECTS/term cap → **over the per-term cap by 1 ECTS**.

## Conflicts found (`detect_plan_conflicts`)
- **credit_overload**, term 1: LINALG + ML + PROB = 13 ECTS vs. 12 ECTS cap.
- No duplicate-enrollment or internal prerequisite-order conflicts.

## Fix attempt for the term-1 overload
LINALG is the course in term 1 that is not in `target_courses`, so I checked whether it could be waived against your known concepts (Bayes' rule, Combinations, Permutations) via `suggest_substitution`:
- **Result: not waivable.** LINALG has 38 concepts total; none matched your known concepts, leaving 38 residual concepts (well over the max_residual_concepts=5 threshold).
- **Fix attempt failed** — I did not re-run `detect_plan_conflicts` with LINALG removed, since the waiver itself was rejected. There is nothing else in term 1 outside `target_courses` to try instead.
- **Recommendation:** move LINALG to a different term rather than trying to waive it. E.g. LINALG alone in term 2 (or ML/PROB split across two terms) would bring term 1 back under the 12 ECTS cap.

## Redundancy check for PROB (per your note)
You asked whether Probability essentials is mostly redundant given your prior bootcamp knowledge. Checked via `suggest_substitution`:
- PROB has 34 concepts total. Only 3 are covered by your known concepts (Bayes' rule, Combinations, Permutations) — everything else (conditional probability, expected value, variance, set theory, combinatorics word problems, total probability law, etc.) is residual.
- **Result: not waivable** — 31 residual concepts, far above the max_residual_concepts=5 threshold.
- **Conclusion: PROB is not redundant.** Your prior knowledge covers only a small intro slice; the course should stay in the plan at full ECTS (4 ECTS).

## External prerequisites the system could not verify
`validate_study_plan` flagged these as advisory (outside this dataset, not checked against your history):
- **ML** requires: "Programming Concepts"
- **ML** requires: "Data Manipulation Essentials"

You should confirm on your own that these are satisfied before enrolling in ML.

## Internal prerequisites
No internal prerequisite violations among CALC/LINALG/ML/PROB — none of these four requires another per the current dataset.

## Bottom line
- Target-course ECTS is within your 20 ECTS budget.
- Term 1 as planned is over its 12 ECTS/term cap by 1 ECTS; waiving LINALG doesn't fix it (not waivable), so reschedule a course to another term instead.
- PROB is not a good waiver candidate — keep it in the plan.
- Double-check ML's two external prerequisites yourself; the tools can't verify them.
