from __future__ import annotations

import os
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .client import LinkedInMDPClient

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

mcp = MCPServer(
    "linkedin-member-data-portability",
    instructions=(
        "Read-only access to the authenticated member's LinkedIn Member Data Portability data. "
        "This server exposes no LinkedIn write action and no arbitrary HTTP request capability. "
        "Historical invitation rows do not prove pending/rejected/withdrawn/expired state. "
        "Do not infer message read/unread state from a missing readAt field."
    ),
)


def _client() -> LinkedInMDPClient:
    return LinkedInMDPClient.from_env()


async def _with_client(call):
    client = _client()
    try:
        return await call(client)
    finally:
        await client.aclose()


@mcp.tool(title="LinkedIn authorization status", annotations=READ_ONLY)
async def linkedin_authorization_status() -> dict[str, Any]:
    """Return Member Data Portability authorization information for the authenticated member/app."""
    return await _with_client(lambda c: c.authorization_status())


@mcp.tool(title="LinkedIn connections snapshot", annotations=READ_ONLY)
async def linkedin_connections(max_pages: int = 10) -> dict[str, Any]:
    """Return raw CONNECTIONS snapshot rows, including current first-degree connections."""
    return await _with_client(lambda c: c.snapshot("CONNECTIONS", max_pages=max_pages))


@mcp.tool(title="LinkedIn invitations snapshot", annotations=READ_ONLY)
async def linkedin_invitations(max_pages: int = 10) -> dict[str, Any]:
    """Return raw INVITATIONS history. It does not expose authoritative invitation lifecycle state."""
    return await _with_client(lambda c: c.snapshot("INVITATIONS", max_pages=max_pages))


@mcp.tool(title="LinkedIn inbox snapshot", annotations=READ_ONLY)
async def linkedin_inbox(max_pages: int = 10) -> dict[str, Any]:
    """Return raw INBOX rows for sent/received message history and conversations."""
    return await _with_client(lambda c: c.snapshot("INBOX", max_pages=max_pages))


@mcp.tool(title="LinkedIn member changelog", annotations=READ_ONLY)
async def linkedin_member_changelog(
    start_time: int | None = None,
    count: int = 50,
    max_pages: int = 5,
) -> dict[str, Any]:
    """Return recent Member Data Portability changelog events. start_time is epoch milliseconds."""
    return await _with_client(
        lambda c: c.changelog(start_time=start_time, count=count, max_pages=max_pages)
    )


def main() -> None:
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
