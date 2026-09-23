from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

import linkedin_mdp_mcp.server as server


class FakeClient:
    async def aclose(self) -> None:
        return None

    async def authorization_status(self) -> dict[str, Any]:
        return {"elements": [{"status": "AUTHORIZED"}]}

    async def snapshot(self, domain: str, *, max_pages: int = 10) -> dict[str, Any]:
        return {"domain": domain, "rows": [{"test": True}], "page_count": 1, "truncated": False}

    async def changelog(self, *, start_time=None, count=50, max_pages=5) -> dict[str, Any]:
        return {"events": [{"processedAt": 123}], "next_start_time": 123}


@pytest.mark.asyncio
async def test_mcp_exposes_exactly_five_read_only_tools(monkeypatch):
    monkeypatch.setattr(server, "_client", lambda: FakeClient())

    async with Client(server.mcp) as client:
        listed = await client.list_tools()
        tools = listed.tools
        names = {tool.name for tool in tools}
        assert names == {
            "linkedin_authorization_status",
            "linkedin_connections",
            "linkedin_invitations",
            "linkedin_inbox",
            "linkedin_member_changelog",
        }
        assert all(tool.annotations and tool.annotations.read_only_hint is True for tool in tools)
        assert all(tool.annotations and tool.annotations.open_world_hint is False for tool in tools)

        result = await client.call_tool("linkedin_connections", {})
        assert result.is_error is False
        assert result.structured_content["domain"] == "CONNECTIONS"
