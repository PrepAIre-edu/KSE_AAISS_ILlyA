---
reviewed_by: curriculum-planning-assistant
source: StudyPlan.md
---

# Study Plan Review

## ECTS Budget
- `validate_study_plan` on target_courses [ML, PROB] (completed: CALC): **total = 10 ECTS**, budget = 20 ECTS → **within budget**, not over.
- No internal prerequisite violations between CALC/ML/PROB.
- Recommended order: **ML → PROB**.

## Term Plan Conflict
- `detect_plan_conflicts` on the term-1 load [LINALG, ML, PROB] with a 12 ECTS/term cap found:
  - **credit_overload** in term 1: LINALG + ML + PROB total **13 ECTS**, exceeding the 12 ECTS cap by 1.
  - Fix: move one course (e.g. LINALG, 3 ECTS) to a later term, or raise the per-term cap.

## Unverifiable External Prerequisites
- ML lists two external prerequisites this system cannot check against your record: **"Programming Concepts"** and **"Data Manipulation Essentials"**. Confirm you've covered these elsewhere before enrolling.

## Waiver Check — PROB
- Checked via `suggest_substitution` against known_concepts (Bayes' rule, Combinations, Permutations):
  - PROB has **34 total concepts**; only **3 covered**, **31 residual** (e.g. conditional probability, expected value, variance, set theory, rules of probability, etc.).
  - **Verdict: not waivable** (residual_count 31 far exceeds the max_residual_concepts limit of 5).
  - Recommendation: take the full PROB course (4 ECTS) — self-study would need to cover the large majority of the syllabus anyway.

## Summary
- ECTS budget: OK (10/20).
- Term 1 schedule: **over by 1 ECTS** — needs rebalancing.
- ML's two external prerequisites: unverified, confirm manually.
- PROB waiver: **denied** — not meaningfully redundant with prior knowledge.
