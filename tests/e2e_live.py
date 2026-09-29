from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from mcp import Client

LINKEDIN_BASE_URL = "https://api.linkedin.com"
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


def _reference_next_link(payload: dict[str, Any]) -> str | None:
    """Return the next LinkedIn page URL while refusing any non-LinkedIn host."""
    paging = payload.get("paging")
    links = paging.get("links") if isinstance(paging, dict) else None
    if not isinstance(links, list):
        return None

    for link in links:
        if not isinstance(link, dict) or str(link.get("rel", "")).lower() != "next":
            continue
        href = link.get("href")
        if not isinstance(href, str) or not href:
            continue
        candidate = urljoin(LINKEDIN_BASE_URL, href)
        parsed = urlparse(candidate)
        if parsed.scheme != "https" or parsed.netloc != "api.linkedin.com":
            raise AssertionError("Reference pagination returned an unexpected host")
        return candidate
    return None


async def _fetch_connections_reference(
    *,
    max_pages: int = 10,
) -> tuple[list[Any], int, bool]:
    """Fetch CONNECTIONS pages directly as an oracle independent from the MCP client."""
    token = (os.getenv("LINKEDIN_ACCESS_TOKEN") or os.getenv("LINKEDIN_TOKEN") or "").removeprefix(
        "Bearer "
    ).strip()
    if not token:
        raise RuntimeError("Live E2E requires LINKEDIN_ACCESS_TOKEN or LINKEDIN_TOKEN")

    headers = {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": os.getenv("LINKEDIN_API_VERSION", "202312"),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = f"{LINKEDIN_BASE_URL}/rest/memberSnapshotData"
    params: dict[str, Any] | None = {"q": "criteria", "domain": "CONNECTIONS"}
    elements: list[Any] = []
    page_count = 0

    async with httpx.AsyncClient(timeout=45.0) as http:
        while page_count < max_pages:
            for attempt in range(3):
                try:
                    response = await http.get(url, params=params, headers=headers)
                except httpx.HTTPError as exc:
                    if attempt >= 2:
                        raise RuntimeError(
                            f"Direct CONNECTIONS reference failed with a network error: {exc}"
                        ) from exc
                    await asyncio.sleep(0.25 * (2**attempt))
                    continue

                if response.is_success:
                    break
                if response.status_code not in {429, 500, 502, 503, 504} or attempt >= 2:
                    raise RuntimeError(
                        f"Direct CONNECTIONS reference failed with HTTP {response.status_code}"
                    )
                await asyncio.sleep(0.25 * (2**attempt))
            else:
                raise AssertionError("unreachable")

            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError("Direct CONNECTIONS reference returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise RuntimeError("Direct CONNECTIONS reference returned a non-object payload")

            current = payload.get("elements", [])
            if isinstance(current, list):
                elements.extend(current)
            page_count += 1

            next_url = _reference_next_link(payload)
            if next_url is None:
                return elements, page_count, False
            url = next_url
            params = None

    return elements, page_count, True


def _assert_oracle_rejects_legacy_single_snapshot() -> None:
    """Prove the structural oracle rejects the exact single-snapshot defect fixed by this PR."""
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
    expected_rows, _ = _distinct_snapshot_rows(raw_elements)
    legacy_rows = _legacy_single_snapshot_rows(raw_elements)
    if _same_json_value(legacy_rows, expected_rows):
        raise AssertionError("Verifier self-check failed to reject legacy single-snapshot data loss")


def _assert_connections_match_reference(
    connections: dict[str, Any],
    reference_elements: list[Any],
    reference_page_count: int,
    reference_truncated: bool,
) -> tuple[list[Any], int]:
    """Verify MCP CONNECTIONS matches a separately fetched paginated LinkedIn reference."""
    reference_rows, reference_versions = _distinct_snapshot_rows(reference_elements)
    mcp_raw_rows, _ = _distinct_snapshot_rows(connections.get("raw_elements", []))
    actual_rows = connections.get("rows")
    if not isinstance(actual_rows, list):
        raise AssertionError("Connections rows is not a list")
    if reference_page_count < 2:
        raise AssertionError("Connections E2E reference did not exercise snapshot pagination")
    if connections.get("page_count") != reference_page_count:
        raise AssertionError("MCP CONNECTIONS page count differs from the direct LinkedIn reference")
    if connections.get("truncated") is not reference_truncated:
        raise AssertionError("MCP CONNECTIONS truncation differs from the direct LinkedIn reference")
    if not _same_json_value(mcp_raw_rows, reference_rows):
        raise AssertionError("MCP pagination omitted or altered rows from the direct LinkedIn reference")
    if not _same_json_value(actual_rows, reference_rows):
        raise AssertionError("Connections snapshot dropped, duplicated, or altered reference rows")
    return reference_rows, reference_versions


async def main() -> None:
    """Run the live LinkedIn MCP contract and independent pagination checks, then write a summary."""
    _assert_oracle_rejects_legacy_single_snapshot()
    endpoint = os.getenv("MCP_ENDPOINT", "http://127.0.0.1:8000/mcp")

    async with Client(endpoint) as client:
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        if names != EXPECTED_TOOLS:
            raise AssertionError(f"Unexpected MCP surface: {sorted(names)}")

        auth = await _call(client, "linkedin_authorization_status", {})
        reference_elements, reference_page_count, reference_truncated = (
            await _fetch_connections_reference(max_pages=10)
        )
        connections = await _call(client, "linkedin_connections", {"max_pages": 10})
        invitations = await _call(client, "linkedin_invitations", {"max_pages": 1})
        inbox = await _call(client, "linkedin_inbox", {"max_pages": 1})
        changelog = await _call(
            client,
            "linkedin_member_changelog",
            {"count": 10, "max_pages": 1},
        )

        reference_rows, reference_versions = _assert_connections_match_reference(
            connections,
            reference_elements,
            reference_page_count,
            reference_truncated,
        )
        legacy_connection_rows = _legacy_single_snapshot_rows(reference_elements)
        live_legacy_would_differ = not _same_json_value(
            reference_rows,
            legacy_connection_rows,
        )
        if not live_legacy_would_differ:
            raise AssertionError(
                "Live CONNECTIONS data does not currently distinguish the legacy selector; "
                "the defect-sensitive proof is inconclusive"
            )

        summary = {
            "protocol_version": str(client.protocol_version),
            "tools": sorted(names),
            "authorization_keys": sorted(auth.keys()),
            "connections_rows": len(connections.get("rows", [])),
            "connections_distinct_reference_rows": len(reference_rows),
            "connections_reference_snapshot_versions": reference_versions,
            "connections_page_count": connections.get("page_count"),
            "connections_reference_page_count": reference_page_count,
            "connections_legacy_single_snapshot_rows": len(legacy_connection_rows),
            "connections_live_legacy_would_differ": live_legacy_would_differ,
            "connections_oracle_self_check": True,
            "invitations_rows": len(invitations.get("rows", [])),
            "inbox_rows": len(inbox.get("rows", [])),
            "changelog_events": len(changelog.get("events", [])),
            "connections_truncated": connections.get("truncated"),
            "connections_reference_truncated": reference_truncated,
            "invitations_truncated": invitations.get("truncated"),
            "inbox_truncated": inbox.get("truncated"),
            "changelog_truncated": changelog.get("truncated"),
        }
        summary_path = Path(os.getenv("E2E_SUMMARY_PATH", "e2e-summary.json"))
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with summary_path.open("w", encoding="utf-8") as artifact:
            json.dump(summary, artifact, indent=2, sort_keys=True)
            artifact.write("\n")
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
