"""Study-planner agent — Claude Agent SDK driver that ties both MCP servers
into one workflow:

  1. read the student's plan from the Obsidian demo vault (existing MCP,
     Part A) — StudyPlan.md's frontmatter names target/completed courses,
     a term-by-term schedule, and concepts the student already knows;
  2. validate and analyze that plan through curriculum-mcp (custom MCP,
     Part B) — prerequisites/ECTS budget, term conflicts, waiver
     feasibility for a course the student may already know;
  3. write a review note back into the vault summarizing the findings —
     the Obsidian read result and the curriculum-mcp results both feed the
     final output, satisfying "tool result affects a later step".

Run independently of both MCP servers (they are separate processes the SDK
launches/connects to on demand — curriculum-mcp as a stdio subprocess,
Obsidian as an HTTP connection to the already-running plugin):

    python -m study_planner.study_planner_agent [note.md]

The optional argument names a different plan note in the vault (default
StudyPlan.md) — useful to vary the input during the defence, including
pointing at a note that does not exist to demonstrate failure handling.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    query,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

SYSTEM_PROMPT = """\
You are a curriculum-planning assistant for a 4-course AI-prep track \
(CALC, LINALG, ML, PROB). You have two tool namespaces:

- obsidian tools (mcp__obsidian__*): read/write notes in the student's vault.
- curriculum tools (mcp__curriculum__*): validate_study_plan, \
detect_plan_conflicts, suggest_substitution, compare_courses — domain \
operations over the real course/concept data. These are the ONLY source of \
truth for prerequisites, ECTS, and concept coverage; never guess those \
numbers yourself.

Workflow for a review request:
1. Read the student's plan note via the obsidian vault_read tool.
2. Call the relevant curriculum tools based on what the note asks: at least \
validate_study_plan and detect_plan_conflicts using the note's own \
target_courses/completed_courses/term_plan/budget fields. If the note lists \
known_concepts and questions whether a course is redundant, also call \
suggest_substitution for that course.
3. Write a new note back into the vault (obsidian vault_write) with a short, \
concrete review: total ECTS and whether it's over budget, any conflicts \
found (name them), external prerequisites the system could not verify, and \
the waiver verdict if you checked one. Cite the actual numbers the tools \
returned — do not restate the student's own note back at them.

If a tool call fails for any reason (missing note, unreachable vault, \
invalid input, unknown course code), do not guess or invent a plausible-\
looking result to fill the gap. Stop, state plainly which tool failed and \
what the error said, and tell the user what to fix.
"""

def _user_prompt(note: str) -> str:
    stem = note[:-3] if note.endswith(".md") else note
    return (f'Review the plan in {note} and write your findings to '
            f'"{stem} Review.md" in the same vault.')


def _load_dotenv() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _build_options() -> ClaudeAgentOptions:
    obsidian_key = os.environ.get("OBSIDIAN_API_KEY")
    obsidian_url = os.environ.get("OBSIDIAN_BASE_URL", "http://127.0.0.1:27123").rstrip("/") + "/mcp/"
    if not obsidian_key or obsidian_key.startswith("paste-"):
        raise SystemExit("OBSIDIAN_API_KEY is not set — see .env.example / README (Obsidian setup).")

    # ANTHROPIC_API_KEY is deliberately optional: with it unset (or left as the
    # .env.example placeholder), the Claude Code CLI falls back to whatever
    # claude.ai account is already logged in on this machine (a Pro/Max
    # subscription), so the agent runs with no separate API billing. Set a
    # real key here only to bill this run to a pay-per-token API account
    # instead of the ambient login.
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key in {"sk-ant-...", "sk-ant-"}:
        del os.environ["ANTHROPIC_API_KEY"]

    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={
            "curriculum": {
                "type": "stdio",
                "command": "python",
                "args": ["-m", "mcp_servers.curriculum_mcp.server"],
            },
            "obsidian": {
                "type": "http",
                "url": obsidian_url,
                "headers": {"Authorization": f"Bearer {obsidian_key}"},
            },
        },
        # Both MCP servers are local, developer-controlled processes acting only
        # on a dedicated demo vault and a static local dataset — no destructive
        # or external side effect exists for this run to gate behind a prompt.
        permission_mode="bypassPermissions",
        cwd=str(REPO_ROOT),
        model=os.environ.get("STUDY_PLANNER_MODEL", "claude-sonnet-5"),
    )


async def run() -> None:
    # Windows' console defaults stdout to the system codepage (cp1252 etc),
    # which can't encode plain arrows or non-Latin text that model output or
    # tool payloads may contain; force UTF-8 so a print() never crashes the run.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    _load_dotenv()
    options = _build_options()
    note = sys.argv[1] if len(sys.argv) > 1 else "StudyPlan.md"
    prompt = _user_prompt(note)
    print(f"[prompt] {prompt}\n")

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)
                elif isinstance(block, ToolUseBlock):
                    print(f"\n[tool call] {block.name}({block.input})")
        elif hasattr(message, "content"):  # UserMessage carrying tool results
            for block in getattr(message, "content", []) or []:
                if isinstance(block, ToolResultBlock):
                    status = "ERROR" if block.is_error else "ok"
                    print(f"[tool result: {status}] {str(block.content)[:400]}")
        elif isinstance(message, ResultMessage):
            print(f"\n--- done: {message.num_turns} turns, "
                  f"${message.total_cost_usd or 0:.4f}, "
                  f"{'ERROR' if message.is_error else 'success'} ---")


if __name__ == "__main__":
    asyncio.run(run())
