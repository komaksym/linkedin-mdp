from __future__ import annotations

import httpx
import pytest

from linkedin_mdp_mcp.client import LinkedInAPIError, LinkedInMDPClient


def _snapshot_payload(total=None, start=None, count=None, *, links=()):
    paging = {"links": list(links)}
    if total is not None:
        paging["total"] = total
    if start is not None:
        paging["start"] = start
    if count is not None:
        paging["count"] = count
    return {
        "elements": [{"snapshotDomain": "CONNECTIONS", "snapshotData": [{"First Name": "Ada"}]}],
        "paging": paging,
    }


def _handler_for(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)
    return handler


@pytest.mark.asyncio
async def test_snapshot_strict_missing_next_link_with_more_data_fails_closed():
    payload = _snapshot_payload(total=3, start=0, count=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_for(payload))) as http:
        client = LinkedInMDPClient("test-token", http=http)
        with pytest.raises(LinkedInAPIError, match="no next link"):
            await client.snapshot("CONNECTIONS", strict_elements=True)


@pytest.mark.asyncio
async def test_snapshot_nonstrict_missing_next_link_with_more_data_fails_closed():
    payload = _snapshot_payload(total=3, start=0, count=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_for(payload))) as http:
        client = LinkedInMDPClient("test-token", http=http)
        with pytest.raises(LinkedInAPIError, match="no next link"):
            await client.snapshot("CONNECTIONS")


@pytest.mark.asyncio
async def test_changelog_missing_next_link_with_more_data_fails_closed():
    payload = {"elements": [{"processedAt": 1200}], "paging": {"total": 3, "start": 0, "count": 1, "links": []}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_for(payload))) as http:
        client = LinkedInMDPClient("test-token", http=http)
        with pytest.raises(LinkedInAPIError, match="no next link"):
            await client.changelog(start_time=1000)


@pytest.mark.asyncio
async def test_complete_page_without_next_link_is_ok():
    payload = _snapshot_payload(total=1, start=0, count=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_for(payload))) as http:
        client = LinkedInMDPClient("test-token", http=http)
        result = await client.snapshot("CONNECTIONS", strict_elements=True)
    assert result["rows"] == [{"First Name": "Ada"}]
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_missing_paging_counts_without_next_link_is_ok():
    payload = _snapshot_payload()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_for(payload))) as http:
        client = LinkedInMDPClient("test-token", http=http)
        result = await client.snapshot("CONNECTIONS", strict_elements=True)
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_non_integer_paging_counts_without_next_link_is_ok():
    payload = _snapshot_payload(total="3", start=0, count=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler_for(payload))) as http:
        client = LinkedInMDPClient("test-token", http=http)
        result = await client.snapshot("CONNECTIONS", strict_elements=True)
    assert result["truncated"] is False
