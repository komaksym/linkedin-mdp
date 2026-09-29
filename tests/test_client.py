from __future__ import annotations

import httpx
import pytest

from linkedin_mdp_mcp.client import LinkedInAPIError, LinkedInMDPClient


@pytest.mark.asyncio
async def test_snapshot_flattens_rows_and_follows_next_link():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["LinkedIn-Version"] == "202312"
        if request.url.params.get("start") == "1":
            return httpx.Response(
                200,
                json={"elements": [{"snapshotData": [{"First Name": "Grace"}]}], "paging": {"links": []}},
            )
        return httpx.Response(
            200,
            json={
                "elements": [{"snapshotData": [{"First Name": "Ada"}]}],
                "paging": {
                    "links": [
                        {
                            "rel": "next",
                            "href": "https://api.linkedin.com/rest/memberSnapshotData?q=criteria&domain=CONNECTIONS&start=1",
                        }
                    ]
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LinkedInMDPClient("test-token", http=http)
        result = await client.snapshot("CONNECTIONS")

    assert result["rows"] == [{"First Name": "Ada"}, {"First Name": "Grace"}]
    assert result["page_count"] == 2
    assert result["truncated"] is False
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_snapshot_404_is_empty_not_fatal():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "No data found for this domain and memberId."})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LinkedInMDPClient("test-token", http=http)
        result = await client.snapshot("INBOX")

    assert result["rows"] == []
    assert result["page_count"] == 0


@pytest.mark.asyncio
async def test_changelog_uses_expected_finder_and_cursor():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/memberChangeLogs"
        assert request.url.params["q"] == "memberAndApplication"
        assert request.url.params["count"] == "50"
        assert request.url.params["startTime"] == "1000"
        return httpx.Response(
            200,
            json={
                "elements": [{"processedAt": 1200}, {"processedAt": 1500}],
                "paging": {"links": []},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LinkedInMDPClient("test-token", http=http)
        result = await client.changelog(start_time=1000)

    assert result["next_start_time"] == 1500


@pytest.mark.asyncio
async def test_authorization_uses_expected_finder():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/memberAuthorizations"
        assert request.url.params["q"] == "memberAndApplication"
        return httpx.Response(200, json={"elements": [{"status": "AUTHORIZED"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LinkedInMDPClient("test-token", http=http)
        result = await client.authorization_status()

    assert result["elements"][0]["status"] == "AUTHORIZED"


@pytest.mark.asyncio
async def test_next_link_cannot_exfiltrate_token_to_another_host():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "elements": [],
                "paging": {"links": [{"rel": "next", "href": "https://evil.example/steal"}]},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LinkedInMDPClient("test-token", http=http)
        with pytest.raises(LinkedInAPIError, match="unexpected host"):
            await client.snapshot("CONNECTIONS")
