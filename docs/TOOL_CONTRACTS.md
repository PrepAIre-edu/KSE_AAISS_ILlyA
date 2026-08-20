# Tool Contracts (Part C)

Every custom tool lives in [`mcp_servers/curriculum_mcp/server.py`](../mcp_servers/curriculum_mcp/server.py): each tool's input schema comes from its function signature's `Annotated[type, pydantic.Field(...)]` parameters, and its output schema is a pydantic model in [`models.py`](../mcp_servers/curriculum_mcp/models.py); both are backed by [`graph_store.py`](../mcp_servers/curriculum_mcp/graph_store.py). All examples below are real captured input/output pairs from this repository's dataset (110 concepts, 4 courses: CALC, LINALG, ML, PROB), not invented.

---

## Custom tool 1 — `validate_study_plan`

| Element | Content |
|---|---|
| **Name** | `validate_study_plan` |
| **Purpose** | Check whether a set of courses a student wants to take is internally consistent — prerequisites satisfied, ECTS budget respected — before they enroll. The model should call this first for any plan-review request. |
| **Model-facing description** | *"Validate a set of target courses against known prerequisites and an optional ECTS budget. Checks two things this dataset can actually support: (1) internal prerequisites among CALC/LINALG/ML/PROB themselves (per their own syllabi, currently none of the four requires another — see course_metadata.json), and (2) each target course's OWN external prerequisites, which reference courses outside this dataset and so are surfaced as advisory notes rather than verified. Use before enrolling in a term to catch budget overruns or an out-of-order plan."* |
| **Input schema** | `target_courses: list[str]` (required, min 1 item) — course codes wanted. `completed_courses: list[str]` (optional, default `[]`) — course codes already done. `max_total_ects: int \| None` (optional, default `null`, constraint `> 0`) — ECTS ceiling for the not-yet-completed target courses. |
| **Output schema** | `valid: bool`. `total_ects: int`. `over_budget: bool`. `internal_prerequisite_violations: list[{course_code, missing_prerequisite_course}]`. `external_prerequisite_notes: list[{course_code, note}]` — prerequisites the system cannot verify. `recommended_order: list[str]` — `target_courses` topologically sorted by internal prerequisites. |
| **Error conditions** | Any code in `target_courses`/`completed_courses` not in `{CALC, LINALG, ML, PROB}` → `ToolError` (`isError: true`), message `unknown_course_code: '<code>' is not one of the known courses (CALC, LINALG, ML, PROB)`. `max_total_ects <= 0` → schema validation error before the tool body runs. |
| **Side effects** | None. Read-only over the in-memory graph loaded at server startup. |
| **Example** | Input: `{"target_courses": ["ML", "PROB"], "completed_courses": ["CALC"], "max_total_ects": 20}`. Output: `{"valid": true, "total_ects": 10, "over_budget": false, "internal_prerequisite_violations": [], "external_prerequisite_notes": [{"course_code": "ML", "note": "Programming Concepts"}, {"course_code": "ML", "note": "Data Manipulation Essentials"}], "recommended_order": ["ML", "PROB"]}`. (Captured from a live agent run, 2026-08-19.) |

---

## Custom tool 2 — `detect_plan_conflicts`

| Element | Content |
|---|---|
| **Name** | `detect_plan_conflicts` |
| **Purpose** | Given a *term-by-term* schedule (not just a course set), find scheduling problems: credit overload in a term, a course scheduled twice, or a course scheduled no later than a prerequisite it depends on. Complements `validate_study_plan`, which checks *what* to take, not *when*. |
| **Model-facing description** | *"Check a term-by-term schedule for credit overload, duplicate enrollment, and prerequisite-order conflicts. Unlike validate_study_plan (which checks WHAT to take), this checks WHEN: it groups the plan by term, flags any term whose combined ECTS exceeds max_ects_per_term, flags a course scheduled twice, and — for any internal prerequisite this dataset does record — flags a course scheduled no later than the term of a course it depends on."* |
| **Input schema** | `plan: list[{course_code: str, term: int (>= 1)}]` (required, min 1 item). `max_ects_per_term: int` (optional, default `15`, constraint `> 0`). |
| **Output schema** | `ok: bool` (true iff `conflicts` is empty). `conflicts: list[{type: "duplicate_course" \| "credit_overload" \| "prerequisite_order", term: int \| null, courses: list[str], detail: str}]`. `ects_by_term: dict[str, int]` (term number as string key → total ECTS scheduled). |
| **Error conditions** | Unknown `course_code` in `plan` → `ToolError` (`unknown_course_code: ...`), same format as `validate_study_plan`. `term < 1` or `max_ects_per_term <= 0` → schema validation error. |
| **Side effects** | None. Read-only. |
| **Example** | Input: `{"plan": [{"course_code": "LINALG", "term": 1}, {"course_code": "ML", "term": 1}, {"course_code": "PROB", "term": 1}], "max_ects_per_term": 12}`. Output: `{"ok": false, "conflicts": [{"type": "credit_overload", "term": 1, "courses": ["LINALG", "ML", "PROB"], "detail": "term 1 totals 13 ECTS, over the 12 cap."}], "ects_by_term": {"1": 13}}`. (Captured live — LINALG 3 + ML 6 + PROB 4 = 13 ECTS, over a 12 cap.) |

---

## Custom tool 3 — `suggest_substitution`

| Element | Content |
|---|---|
| **Name** | `suggest_substitution` |
| **Purpose** | Evaluate whether a course can be *waived* — replaced with self-study of a small residual gap — given concepts the student already knows from prior education. This is the "feasible substitution under a credit constraint": the alternative to a waiver is paying the course's full ECTS cost. |
| **Model-facing description** | *"Evaluate whether a course can be waived in favor of self-study, given concepts the student already knows. Matches each known_concepts entry against the course's actual concept names (normalized word-overlap, since these are free-text and course concepts don't share a controlled vocabulary across courses in this dataset). Returns the covered/residual split and whether the residual gap is small enough to approve a waiver under max_residual_concepts — the 'credit constraint': the alternative to a waiver is spending the course's full ECTS load, so this is what validate_study_plan's budget check would act on next."* |
| **Input schema** | `course_code: str` (required). `known_concepts: list[str]` (required, min 1 item) — free-text concept names, not slugs. `max_residual_concepts: int` (optional, default `5`, constraint `>= 0`). |
| **Output schema** | `course_code: str`. `ects: int`. `total_concepts: int`. `covered_concepts: list[str]`. `residual_concepts: list[str]`. `residual_count: int`. `waivable: bool` (`residual_count <= max_residual_concepts`). `note: str` (human-readable summary). |
| **Error conditions** | Unknown `course_code` → `ToolError` (`unknown_course_code: ...`). An empty `known_concepts` list → schema validation error (min 1 item required). An unmatched string in `known_concepts` is **not** an error — it simply contributes nothing to `covered_concepts`; this is how the tool distinguishes "nothing matched" (a normal, structured result) from a real input failure. |
| **Side effects** | None. Read-only. |
| **Example** | Input: `{"course_code": "PROB", "known_concepts": ["Bayes' rule", "Combinations", "Permutations"], "max_residual_concepts": 5}`. Output (truncated): `{"course_code": "PROB", "ects": 4, "total_concepts": 34, "covered_concepts": ["Bayes' rule", "Combinations", "Permutations"], "residual_concepts": ["addition rules", "Basics of logic", "...(28 more)"], "residual_count": 31, "waivable": false, "note": "31 concept(s) not recognized among the 3 known_concepts given; exceeds the max_residual_concepts=5 limit."}`. (Captured live — the 3 known concepts leave 31 of PROB's 34 concepts uncovered, so the waiver is denied.) |

---

## Custom tool 4 (bonus) — `compare_courses`

| Element | Content |
|---|---|
| **Name** | `compare_courses` |
| **Purpose** | Compare two courses' concept coverage, category mix, and workload — e.g. to decide between electives or check for redundant enrollment. Distinct from the other three: it is a symmetric comparison of two whole courses, not a validation or waiver decision. |
| **Model-facing description** | *"Compare two courses: concept overlap, kind mix (definition/method/metric/principle), ECTS, and external prerequisites. Concept overlap uses the same normalized word-match as suggest_substitution; an empty related_concepts list is a real, expected result for this dataset (see design-rationale doc) — it means the two courses cover disjoint material by name, not that the comparison failed."* |
| **Input schema** | `course_a: str` (required). `course_b: str` (required). |
| **Output schema** | `course_a, course_b: str`. `ects_a, ects_b: int`. `concept_count_a, concept_count_b: int`. `kind_distribution_a, kind_distribution_b: dict[str, int]`. `related_concepts: list[{concept_in_a, concept_in_b}]`. `unique_to_a, unique_to_b: list[str]`. `external_prerequisites_a, external_prerequisites_b: list[str]`. |
| **Error conditions** | Unknown `course_a`/`course_b` → `ToolError` (`unknown_course_code: ...`). |
| **Side effects** | None. Read-only. |
| **Example** | Input: `{"course_a": "LINALG", "course_b": "PROB"}`. Output (truncated): `{"course_a": "LINALG", "course_b": "PROB", "ects_a": 3, "ects_b": 4, "concept_count_a": 38, "concept_count_b": 34, "kind_distribution_a": {"definition": 26, "metric": 1, "method": 11}, "kind_distribution_b": {"method": 2, "definition": 27, "principle": 2, "metric": 3}, "related_concepts": [], "unique_to_a": ["Algebraic view on vectors", "..."], "unique_to_b": ["addition rules", "..."], "external_prerequisites_a": [], "external_prerequisites_b": []}`. The empty `related_concepts` is expected and documented, not a bug — see the known-limitation note in [DESIGN_RATIONALE.md](DESIGN_RATIONALE.md). |

---

## Shared error-handling contract (all 4 custom tools)

Every tool returns a discriminated result at the MCP layer: a successful call (including one that legitimately finds nothing — no conflicts, no related concepts, an empty residual list) comes back as `isError: false` with structured JSON content; a genuine input failure (unknown course code, schema-invalid input) comes back as `isError: true` with a `unknown_course_code: ...`-prefixed message, raised via the SDK's `ToolError`. A caller can always tell "ran successfully and found nothing" apart from "could not run."

---

## Existing server — Obsidian Local REST API MCP (Part A)

The plugin ([`coddingtonbear/obsidian-local-rest-api`](https://github.com/coddingtonbear/obsidian-local-rest-api)) ships a built-in MCP server (Streamable HTTP transport, bearer-token auth) exposing 16 tools. We document the two actually used by the agent: `vault_read` (reads the student's plan) and `vault_write` (writes the review back). Descriptions below are the exact text the plugin's MCP server returns from `list_tools()`, captured live against our demo vault on 2026-08-19.

### `vault_read`

| Element | Content |
|---|---|
| **Name** | `vault_read` |
| **Purpose (in our project)** | The agent's first step: read the student's plan note (frontmatter: `target_courses`, `completed_courses`, `term_plan`, `known_concepts`, budget fields) so its content can drive which `curriculum-mcp` tools get called next. |
| **Model-facing description** | *"Read a vault file's content and metadata. Returns a JSON object with: content (full markdown text), path, tags (array of tag strings), frontmatter (parsed YAML front-matter as an object), stat ({ctime, mtime, size}), links (array of vault-relative paths this file links to), backlinks (array of vault-relative paths of files that link here), and unresolvedLinks (array of link text in this file that does not resolve to an existing vault file). Throws if the file does not exist. [...] To save context, call vault_get_document_map first [...] and prefer targeted reads over full reads for anything but short files."* |
| **Input schema** | `path: str` (required) — file path relative to vault root. Optional `targetType: "heading" \| "block" \| "frontmatter"` and `target` (string or string array) to read only one section instead of the whole file; optional `scope: "content" \| "marker" \| "markerAndContent"` refining what a targeted read returns. |
| **Output/returned content** | Full object described above (content, path, tags, frontmatter, stat, links, backlinks, unresolvedLinks) when no `targetType`/`target` given; otherwise just the matched section (string or JSON value). |
| **Error conditions / side effects** | Throws (`isError: true`) if the file does not exist — surfaced to the MCP caller as plain text, e.g. `"File not found: DoesNotExist.md"` (captured live). No side effects; read-only. |

### `vault_write`

| Element | Content |
|---|---|
| **Name** | `vault_write` |
| **Purpose (in our project)** | The agent's last step: write the review (ECTS budget verdict, term conflicts, waiver decision) back into the vault as a new note the student can open in Obsidian. |
| **Model-facing description** | *"Create or overwrite a vault file with the given content. Creates any missing parent directories automatically. Overwrites without warning if the file already exists."* |
| **Input schema** | `path: str` (required). `content: str` (required) — full replacement file content. |
| **Output/returned content** | `{"message": "OK"}` on success (captured live). |
| **Error conditions / side effects** | **Side effect, explicit:** creates or silently overwrites the target file — this is why the agent is instructed to write to a distinct `<note> Review.md` path rather than overwrite the student's own plan note. No documented throw condition beyond generic vault-access failure (e.g. the whole server unreachable, covered by the connection-level failure demo below). |

### Realistic failure demonstrated

Two failure modes were reproduced against the live system (not simulated):

1. **Raw MCP client, missing note** (`docs/MCP_ASSIGNMENT_PLAN.md` §3): `vault_read` on a nonexistent path returns `is_error: true`, content `"File not found: DoesNotExist.md"`.
2. **Full agent, missing note** (`python -m study_planner.study_planner_agent NoSuchPlan.md`): the agent calls `vault_read`, receives the same error, and — per its system-prompt instruction not to guess on tool failure — stops and reports the failure and next steps to the user instead of inventing a plan. See the transcript captured in `docs/MCP_ASSIGNMENT_PLAN.md` §3 for the exact output.

### Why this server has a reasonable role in the project

Course-planning naturally starts and ends with a document a student actually reads: the plan they wrote, and the review they get back. Obsidian is that document surface — the agent's read result (the note's frontmatter) determines which `curriculum-mcp` tools are even called and with what arguments, and the `curriculum-mcp` results determine what gets written back. Neither MCP connection is a standalone demo call: each one's output is consumed by the other side of the workflow.
