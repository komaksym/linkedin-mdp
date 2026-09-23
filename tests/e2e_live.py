from __future__ import annotations

import asyncio
import json
import os

from mcp import Client

EXPECTED_TOOLS = {
    "linkedin_authorization_status",
    "linkedin_connections",
    "linkedin_invitations",
    "linkedin_inbox",
    "linkedin_member_changelog",
}


def _structured(result):
    if result.is_error:
        texts = []
        for block in result.content:
            text = getattr(block, "text", None)
            if text:
                texts.append(text)
        raise RuntimeError("MCP tool failed: " + " | ".join(texts))
    return result.structured_content or {}


async def main() -> None:
    endpoint = os.getenv("MCP_ENDPOINT", "http://127.0.0.1:8000/mcp")

    async with Client(endpoint) as client:
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        if names != EXPECTED_TOOLS:
            raise AssertionError(f"Unexpected MCP surface: {sorted(names)}")

        auth = _structured(await client.call_tool("linkedin_authorization_status", {}))
        connections = _structured(await client.call_tool("linkedin_connections", {"max_pages": 2}))
        invitations = _structured(await client.call_tool("linkedin_invitations", {"max_pages": 2}))
        inbox = _structured(await client.call_tool("linkedin_inbox", {"max_pages": 2}))
        changelog = _structured(
            await client.call_tool(
                "linkedin_member_changelog",
                {"count": 10, "max_pages": 1},
            )
        )

        summary = {
            "protocol_version": str(client.protocol_version),
            "tools": sorted(names),
            "authorization_keys": sorted(auth.keys()),
            "connections_rows": len(connections.get("rows", [])),
            "invitations_rows": len(invitations.get("rows", [])),
            "inbox_rows": len(inbox.get("rows", [])),
            "changelog_events": len(changelog.get("events", [])),
            "connections_truncated": connections.get("truncated"),
            "invitations_truncated": invitations.get("truncated"),
            "inbox_truncated": inbox.get("truncated"),
            "changelog_truncated": changelog.get("truncated"),
        }
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
