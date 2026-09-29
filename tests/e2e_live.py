from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from mcp import Client

EXPECTED_TOOLS = {
    "linkedin_authorization_status",
    "linkedin_connections",
    "linkedin_invitations",
    "linkedin_inbox",
    "linkedin_member_changelog",
}


def _structured(result):
    """Return structured MCP content or raise with the tool's text error."""
    if result.is_error:
        texts = []
        for block in result.content:
            text = getattr(block, "text", None)
            if text:
                texts.append(text)
        raise RuntimeError("MCP tool failed: " + " | ".join(texts))
    return result.structured_content or {}


async def _call(client: Client, name: str, args: dict, *, timeout: float = 45.0):
    """Call one MCP tool with a bounded timeout and return structured content."""
    print(f"calling {name}", flush=True)
    result = await asyncio.wait_for(client.call_tool(name, args), timeout=timeout)
    print(f"ok {name}", flush=True)
    return _structured(result)


def _same_json_value(left: Any, right: Any) -> bool:
    """Compare decoded JSON values exactly without using serialization as the oracle."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return False
        return all(_same_json_value(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json_value(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    return left == right


def _distinct_snapshot_rows(raw_elements: Any) -> tuple[list[Any], int]:
    """Collect first-seen exact JSON rows from every valid snapshot element."""
    if not isinstance(raw_elements, list):
        raise AssertionError("Snapshot raw_elements is not a list")

    rows: list[Any] = []
    snapshot_versions = 0
    for element in raw_elements:
        if not isinstance(element, dict) or not isinstance(element.get("snapshotData"), list):
            continue
        snapshot_versions += 1
        for row in element["snapshotData"]:
            if any(_same_json_value(row, existing) for existing in rows):
                continue
            rows.append(row)
    return rows, snapshot_versions


def _legacy_single_snapshot_rows(raw_elements: Any) -> list[Any]:
    """Reproduce the pre-fix last-snapshot selector for verifier sensitivity checks."""
    if not isinstance(raw_elements, list):
        return []
    for element in reversed(raw_elements):
        if not isinstance(element, dict):
            continue
        data = element.get("snapshotData")
        if isinstance(data, list):
            return list(data)
    return []


def _assert_snapshot_rows_complete(
    snapshot: dict[str, Any],
    *,
    require_multiple_pages: bool,
) -> tuple[list[Any], int]:
    """Verify public rows equal the independent exact-distinct projection of raw snapshots."""
    expected_rows, snapshot_versions = _distinct_snapshot_rows(snapshot.get("raw_elements", []))
    actual_rows = snapshot.get("rows")
    if not isinstance(actual_rows, list):
        raise AssertionError("Snapshot rows is not a list")
    if require_multiple_pages and snapshot.get("page_count", 0) < 2:
        raise AssertionError("Connections E2E did not exercise snapshot pagination")
    if not _same_json_value(actual_rows, expected_rows):
        raise AssertionError("Connections snapshot dropped, duplicated, or altered paged snapshot rows")
    return expected_rows, snapshot_versions


def _assert_oracle_rejects_legacy_single_snapshot() -> None:
    """Prove the verifier rejects the exact single-snapshot defect fixed by this PR."""
    raw_elements = [
        {
            "snapshotData": [
                {"id": 1, "name": "Ada"},
                {"id": 2, "active": True},
            ]
        },
        {
            "snapshotData": [
                {"name": "Ada", "id": 1},
                {"id": 2, "active": 1},
                {"id": 3, "name": "Linus"},
            ]
        },
    ]
    legacy_rows = _legacy_single_snapshot_rows(raw_elements)
    probe = {
        "raw_elements": raw_elements,
        "rows": legacy_rows,
        "page_count": 2,
    }
    try:
        _assert_snapshot_rows_complete(probe, require_multiple_pages=True)
    except AssertionError:
        return
    raise AssertionError("Verifier self-check failed to reject legacy single-snapshot data loss")


async def main() -> None:
    """Run the live LinkedIn MCP contract and pagination checks, then write a summary."""
    _assert_oracle_rejects_legacy_single_snapshot()
    endpoint = os.getenv("MCP_ENDPOINT", "http://127.0.0.1:8000/mcp")

    async with Client(endpoint) as client:
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        if names != EXPECTED_TOOLS:
            raise AssertionError(f"Unexpected MCP surface: {sorted(names)}")

        auth = await _call(client, "linkedin_authorization_status", {})
        connections = await _call(client, "linkedin_connections", {"max_pages": 10})
        invitations = await _call(client, "linkedin_invitations", {"max_pages": 1})
        inbox = await _call(client, "linkedin_inbox", {"max_pages": 1})
        changelog = await _call(
            client,
            "linkedin_member_changelog",
            {"count": 10, "max_pages": 1},
        )

        distinct_connection_rows, snapshot_versions = _assert_snapshot_rows_complete(
            connections,
            require_multiple_pages=True,
        )
        legacy_connection_rows = _legacy_single_snapshot_rows(connections.get("raw_elements", []))
        live_legacy_would_differ = not _same_json_value(
            connections.get("rows", []),
            legacy_connection_rows,
        )

        summary = {
            "protocol_version": str(client.protocol_version),
            "tools": sorted(names),
            "authorization_keys": sorted(auth.keys()),
            "connections_rows": len(connections.get("rows", [])),
            "connections_distinct_raw_rows": len(distinct_connection_rows),
            "connections_snapshot_versions": snapshot_versions,
            "connections_page_count": connections.get("page_count"),
            "connections_legacy_single_snapshot_rows": len(legacy_connection_rows),
            "connections_live_legacy_would_differ": live_legacy_would_differ,
            "connections_oracle_self_check": True,
            "invitations_rows": len(invitations.get("rows", [])),
            "inbox_rows": len(inbox.get("rows", [])),
            "changelog_events": len(changelog.get("events", [])),
            "connections_truncated": connections.get("truncated"),
            "invitations_truncated": invitations.get("truncated"),
            "inbox_truncated": inbox.get("truncated"),
            "changelog_truncated": changelog.get("truncated"),
        }
        with open("e2e-summary.json", "w", encoding="utf-8") as artifact:
            json.dump(summary, artifact, indent=2, sort_keys=True)
            artifact.write("\n")
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
