# Defence / Demo Script

Target: 10–15 minutes. Commands assume the repo root as cwd and `.env` already
configured (see `README.md`). Have Obsidian open with `obsidian_demo_vault/`
before starting.

---

## 1. Independent startup and architecture overview (~2 min)

Talking point: two MCP connections — one existing (Obsidian, HTTP), one
custom (`curriculum-mcp`, stdio) — both separate processes from the agent.
Show `README.md`'s architecture diagram.

Start the custom server **on its own**, before touching the agent, to prove
process separation and independent startability:

```bash
python -m mcp_servers.curriculum_mcp.server
```

It prints nothing on stdio startup (that's expected — it's waiting for a
client); leave it running or Ctrl+C it, since the agent will launch its own
copy anyway (this step is only to demonstrate it starts independently).

Confirm Obsidian's plugin is enabled: **Settings → Local REST API** shows the
API key and the "Non-encrypted (HTTP) Server" toggle on.

---

## 2. Existing MCP server inside an agent flow (~2–3 min)

Run the isolated connectivity check first — this is discovery + one
successful call, deliberately separated from the agent so a failure here is
unambiguous:

```bash
python scripts/check_obsidian_connection.py
```

Expected: prints all 16 tools with schemas, then a successful `vault_read` on
`StudyPlan.md`. Point out `vault_read`'s schema on screen (real MCP
`inputSchema`, not a guess) and name one likely error condition ("throws if
the file doesn't exist — we'll show that in step 4").

Explain the role in ~2 sentences (see `docs/DESIGN_RATIONALE.md`): the note's
frontmatter is what decides which `curriculum-mcp` tools get called next —
this connection isn't a standalone demo call, its result drives the rest of
the flow.

---

## 3. Custom MCP end-to-end workflow (~3–4 min)

Run the full agent:

```bash
python -m study_planner.study_planner_agent
```

Narrate as it streams: it calls `vault_read`, then `validate_study_plan`,
`detect_plan_conflicts`, and `suggest_substitution` in turn (all three
required custom tools, plus the bonus `compare_courses` is available on
request — see step 5), then `vault_write`s `StudyPlan Review.md`. Open that
note in Obsidian afterward to show the real, human-readable result.

Point out concretely: `detect_plan_conflicts` should report a
`credit_overload` (LINALG+ML+PROB = 13 ECTS against the demo note's 12 cap),
and `suggest_substitution` should deny a PROB waiver (31 of 34 concepts
uncovered) — both are real computed numbers, not scripted text.

---

## 4. Failure scenario (~2 min)

Point at a note that doesn't exist:

```bash
python -m study_planner.study_planner_agent NoSuchPlan.md
```

Expected: `vault_read` returns `isError: true` ("File not found:
NoSuchPlan.md"); the agent stops, reports the failure plainly, and does not
invent a plan. This is the Part A failure demo (an alternative: temporarily
wrong `OBSIDIAN_API_KEY` in `.env`, or quitting Obsidian, both also produce a
clean reported failure rather than a crash).

For a custom-server-side failure instead/in addition, call a tool with an
unknown course code — e.g. ask the agent "validate a plan for course code XYZ"
— and show the `unknown_course_code: ...` `ToolError`, distinguishing it from
a normal empty-but-successful result (see `docs/TOOL_CONTRACTS.md`, "Shared
error-handling contract").

---

## 5. Instructor questions and variation (~3–4 min)

Likely asks and how to answer live:

- **"Vary a valid input."** Edit `obsidian_demo_vault/StudyPlan.md`'s
  frontmatter (e.g. change `max_ects_per_term` to 20) and rerun the agent —
  the credit-overload conflict should disappear.
- **"Show `compare_courses`."** Ask the agent directly: *"Compare LINALG and
  PROB using the curriculum tools."* Expect `related_concepts: []` — explain
  why that's a real, documented result (`docs/DESIGN_RATIONALE.md`), not a
  bug.
- **"Trace one value from source to output."** ECTS numbers: point to the
  literal syllabus text in `output/txt/Syllabus_Probability_essentials.txt`
  ("Amount of ECTS credits: 4 credits") → `mcp_servers/curriculum_mcp/
  course_metadata.json`'s `"ects": 4` with matching `provenance` quote →
  `suggest_substitution`'s output `"ects": 4` → the written review note.
- **"Explain a design decision."** The `suggest_substitution` waiver
  threshold (`max_residual_concepts`), or why HTTP not HTTPS for Obsidian
  (self-signed cert vs. the Node CLI) — both covered in
  `docs/DESIGN_RATIONALE.md`.
- **"Invalid input case."** Call `validate_study_plan` with
  `max_total_ects: -5` (violates the `gt=0` constraint) or a target course
  code not in `{CALC, LINALG, ML, PROB}`.

## Evidence checklist (what must be visible by the end)

- [ ] Custom server started independently, before the agent (step 1)
- [ ] Both MCP connections discovered by the agent (tool list printed in step 3's run)
- [ ] Existing-server tool called successfully, result feeding a later step (step 2–3)
- [ ] Complete custom-server workflow: all 3 required tools + the bonus tool shown across steps 3 and 5
- [ ] One custom tool's contract and one design decision explained (steps 2, 5)
- [ ] One realistic failure reproduced and its handling shown (step 4)
