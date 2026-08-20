"""One-off connectivity check for the Obsidian Local REST API MCP server —
run this once after installing the plugin, to confirm before wiring the
agent: (1) the server is reachable, (2) tool discovery works, (3) at least
one tool call succeeds. This is exactly Part A's minimum bar, checked in
isolation from the agent so a failure here is easy to diagnose.

Usage (after filling in .env — see .env.example):
    python scripts/check_obsidian_connection.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


async def main() -> None:
    # Windows' console defaults stdout to the system codepage, which can't
    # encode every character a tool description might contain; force UTF-8
    # so printing discovered tools never crashes the check.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    _load_dotenv()
    api_key = os.environ.get("OBSIDIAN_API_KEY")
    base_url = os.environ.get("OBSIDIAN_BASE_URL", "http://127.0.0.1:27123")
    if not api_key or api_key.startswith("paste-"):
        raise SystemExit(
            "OBSIDIAN_API_KEY is not set. Copy .env.example to .env, install the "
            "'Local REST API' plugin in a dedicated Obsidian vault, open its "
            "settings (Settings -> Local REST API), and paste the API key shown "
            "there into .env."
        )

    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    url = base_url.rstrip("/") + "/mcp/"
    print(f"connecting to {url} ...")
    http_client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {api_key}"}, verify=False)
    async with streamable_http_client(url, http_client=http_client) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"\ndiscovered {len(tools.tools)} tools:")
            for t in tools.tools:
                print(f"  - {t.name}: {t.description}")
                print(f"    input schema: {json.dumps(t.input_schema)}")

            print("\ncalling vault_read on StudyPlan.md ...")
            result = await session.call_tool("vault_read", {"path": "StudyPlan.md"})
            if result.is_error:
                print("call reported an error (see below) — this is still useful: it tells "
                      "us the real parameter name expected, since discovery above printed "
                      "the exact input schema.")
            print(result.content[0].text if result.content else result)


if __name__ == "__main__":
    asyncio.run(main())
