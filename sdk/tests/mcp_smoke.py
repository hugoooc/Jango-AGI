"""Manual end-to-end smoke test for the Streamable-HTTP engineering MCP server."""

from __future__ import annotations

import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    endpoint = os.environ.get("CHIEF_MCP_URL", "http://127.0.0.1:8770/mcp")
    async with streamablehttp_client(endpoint) as (reader, writer, _session_id):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            expected = {
                "list_capabilities", "list_missions", "start_mission",
                "get_mission", "get_mission_events", "wait_for_mission",
                "get_improvement_proposal",
            }
            missing = expected - names
            if missing:
                raise RuntimeError(f"missing MCP tools: {sorted(missing)}")

            capabilities = await session.call_tool("list_capabilities", {})
            if capabilities.isError:
                raise RuntimeError("capability discovery failed")

            started = await session.call_tool("start_mission", {
                "goal": "Minimize mass",
                "workers": 2,
                "cycles": 1,
            })
            if started.isError or not started.structuredContent:
                raise RuntimeError("mission launch failed")
            started_payload = started.structuredContent.get("result", started.structuredContent)
            mission_id = started_payload["mission_id"]

            result = None
            cursor = 0
            for _ in range(20):
                waited = await session.call_tool("wait_for_mission", {
                    "mission_id": mission_id,
                    "timeout_seconds": 2,
                    "after": cursor,
                })
                if waited.isError or not waited.structuredContent:
                    raise RuntimeError("mission wait failed")
                result = waited.structuredContent.get("result", waited.structuredContent)
                cursor = int(result["next_after"])
                if result["terminal"]:
                    break
            if not result or not result["terminal"]:
                raise RuntimeError("mission did not reach a terminal state")
            if result["mission"]["state"] != "complete":
                raise RuntimeError(json.dumps(result["mission"], indent=2))
            improvement = await session.call_tool("get_improvement_proposal", {
                "mission_id": mission_id,
            })
            if improvement.isError or not improvement.structuredContent:
                raise RuntimeError("improvement proposal failed")
            improvement_payload = improvement.structuredContent.get(
                "result", improvement.structuredContent,
            )
            if improvement_payload.get("auto_promote") is not False:
                raise RuntimeError("improvement proposal must never auto-promote")
            print(json.dumps({
                "tools": sorted(names),
                "mission_id": mission_id,
                "state": result["mission"]["state"],
                "event_cursor": cursor,
                "winner": result["mission"]["result"]["winner"],
                "improvement": improvement_payload,
            }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
