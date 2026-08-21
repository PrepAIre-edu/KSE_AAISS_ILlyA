---
reviewed: 2026-08-22
source: StudyPlan.md
---

# Study Plan Review

## ECTS Budget
- Target courses (ML, PROB) total **10 ECTS** against a 20 ECTS budget — well **under budget**, not over.
- `validate_study_plan` reports the plan as valid with no internal prerequisite violations, and recommends taking **ML before PROB**.

## Unverifiable External Prerequisites
ML has two external prerequisites that fall outside this dataset and could not be verified by the system:
- **Programming Concepts**
- **Data Manipulation Essentials**

You'll need to confirm on your own that these are satisfied before enrolling in ML.

## Term Plan Conflict
`detect_plan_conflicts` flags a **credit overload** in Term 1:
- LINALG + ML + PROB together total **13 ECTS**, exceeding your 12 ECTS/term cap.
- No prerequisite-order or duplicate-enrollment issues were found — this is purely a load problem.
- Fix: move one of the three courses (e.g. LINALG, since it's not one of your stated target courses) to a later term.

## Probability (PROB) Waiver Check
`suggest_substitution` evaluated PROB against your known concepts (Bayes' rule, Combinations, Permutations):
- PROB has **34 total concepts**; only **3 are covered** by what you already know.
- **31 residual concepts** remain uncovered (e.g. conditional probability, expected value, variance, set theory, total probability law, and more).
- Verdict: **not waivable** — the residual gap (31) far exceeds the max allowed for a waiver (5). Probability essentials is **not redundant**; you'd be skipping the large majority of the syllabus.

## Summary
- Budget: OK (10/20 ECTS).
- Term 1: over cap by 1 ECTS (13 vs 12) — rebalance the schedule.
- External prereqs for ML: unverified, confirm manually.
- PROB waiver: denied by the tool (31 residual concepts vs. 5 allowed).
