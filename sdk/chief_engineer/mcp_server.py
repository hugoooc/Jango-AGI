"""Streamable-HTTP MCP boundary for the Jango engineering runtime.

Introspection agents use these tools to discover whatever engineering
adapters are installed and to operate missions without receiving solver or
infrastructure credentials.  The MCP process talks to the mission-control
HTTP API, so browser observers and hosted agents share one durable truth.
"""

from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


API_URL = os.environ.get("CHIEF_ENGINEER_API_URL", "http://127.0.0.1:8765").rstrip("/")
HOST = os.environ.get("CHIEF_MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("CHIEF_MCP_PORT", "8770"))
TERMINAL_STATES = {"complete", "incomplete", "failed"}


def _allowed_hosts() -> list[str]:
    configured = os.environ.get("CHIEF_MCP_ALLOWED_HOSTS", "")
    return [
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
        *(host.strip() for host in configured.split(",") if host.strip()),
    ]

mcp = FastMCP(
    "Jango",
    instructions=(
        "Capability-driven engineering execution. Discover adapters first, then launch and "
        "inspect missions. Solver metrics and feasibility gates are deterministic evidence."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/mcp",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts(),
        allowed_origins=[],
    ),
)


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        API_URL + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"engineering API returned HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"engineering API is unavailable at {API_URL}: {exc.reason}") from exc


@mcp.tool()
def list_capabilities() -> list[dict[str, Any]]:
    """Discover installed software adapters, analyses, metrics, parameters, limits, and artifacts."""
    return _request("GET", "/api/capabilities")


@mcp.tool()
def list_missions() -> list[dict[str, Any]]:
    """List recent engineering missions and their current durable state."""
    return _request("GET", "/api/missions")


@mcp.tool()
def start_mission(
    goal: str,
    initial_design: dict[str, float] | None = None,
    workers: int = 12,
    cycles: int = 3,
) -> dict[str, Any]:
    """Start an autonomous mission using real adapter-declared metrics and parallel workers.

    The goal must name canonical metrics returned by list_capabilities. Constraints should be
    explicit and measurable. workers is capped by the service; cycles is the Chief review budget.
    """
    if not goal.strip():
        raise ValueError("goal must not be empty")
    if workers < 1 or cycles < 1:
        raise ValueError("workers and cycles must be positive")
    payload: dict[str, Any] = {
        "goal": goal.strip(),
        "workers": min(int(workers), 32),
        "cycles": min(int(cycles), 8),
    }
    if initial_design is not None:
        payload["initial_design"] = {str(key): float(value) for key, value in initial_design.items()}
    return _request("POST", "/api/missions", payload)


@mcp.tool()
def get_mission(mission_id: str) -> dict[str, Any]:
    """Read one mission's plan, state, winner, deterministic metrics, and constraint violations."""
    return _request("GET", f"/api/missions/{urllib.parse.quote(mission_id, safe='')}")


@mcp.tool()
def get_mission_events(mission_id: str, after: int = 0) -> dict[str, Any]:
    """Read durable real-time orchestration events after a sequence number without blocking."""
    path = f"/api/missions/{urllib.parse.quote(mission_id, safe='')}/events.json?after={max(0, int(after))}"
    return _request("GET", path)


@mcp.tool()
def get_improvement_proposal(mission_id: str) -> dict[str, Any]:
    """Derive a bounded, evidence-linked runtime improvement proposal; never mutates production."""
    path = f"/api/missions/{urllib.parse.quote(mission_id, safe='')}/improvement"
    return _request("GET", path)


@mcp.tool()
def wait_for_mission(mission_id: str, timeout_seconds: int = 45, after: int = 0) -> dict[str, Any]:
    """Wait briefly for mission progress or completion and return state plus newly observed events."""
    timeout = max(1, min(int(timeout_seconds), 60))
    deadline = time.monotonic() + timeout
    cursor = max(0, int(after))
    collected: list[dict[str, Any]] = []
    mission: dict[str, Any] = {}
    while time.monotonic() < deadline:
        event_page = get_mission_events(mission_id, cursor)
        events = event_page.get("events", [])
        if events:
            collected.extend(events)
            cursor = int(event_page.get("next_after", cursor))
        mission = get_mission(mission_id)
        if mission.get("state") in TERMINAL_STATES or events:
            break
        time.sleep(0.75)
    return {
        "mission": mission,
        "events": collected,
        "next_after": cursor,
        "terminal": mission.get("state") in TERMINAL_STATES,
    }


async def _health(_request: Request) -> JSONResponse:
    try:
        upstream = _request_json_health()
        return JSONResponse({"service": "jango-engineering-mcp", "upstream": upstream})
    except RuntimeError as exc:
        return JSONResponse({"service": "jango-engineering-mcp", "error": str(exc)}, status_code=503)


def _request_json_health() -> dict[str, Any]:
    value = _request("GET", "/health")
    return value if isinstance(value, dict) else {"status": "unexpected"}


class BearerAuthMiddleware:
    """Keep the public MCP endpoint closed; Introspection injects this header at egress."""

    def __init__(self, app, token: str | None):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") != "/health" and self.token:
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            supplied = headers.get(b"authorization", b"").decode("utf-8", errors="ignore")
            expected = f"Bearer {self.token}"
            if not secrets.compare_digest(supplied, expected):
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def app():
    application = mcp.streamable_http_app()
    application.router.routes.append(Route("/health", _health, methods=["GET"]))
    return BearerAuthMiddleware(application, os.environ.get("CHIEF_MCP_TOKEN"))


def main() -> None:
    uvicorn.run(app(), host=HOST, port=PORT, log_level=os.environ.get("CHIEF_MCP_LOG_LEVEL", "info"))


if __name__ == "__main__":
    main()
