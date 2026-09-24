from __future__ import annotations

import httpx
import pytest

from linkedin_mdp_mcp.supabase_client import SupabaseAPIError, SupabaseClient


@pytest.mark.asyncio
async def test_prospect_lookup_paginates_without_silently_dropping_rows():
    seen_offsets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "project.supabase.co"
        assert request.headers["apikey"] == "service-secret"
        assert request.headers["Authorization"] == "Bearer service-secret"
        assert request.url.path == "/rest/v1/prospects"
        assert request.url.params["select"] == "id,linkedin_url_key"
        assert request.url.params["linkedin_url_key"] == "not.is.null"
        seen_offsets.append(request.url.params["offset"])

        if request.url.params["offset"] == "0":
            return httpx.Response(
                200,
                json=[
                    {"id": "p1", "linkedin_url_key": "https://www.linkedin.com/in/one"},
                    {"id": "p2", "linkedin_url_key": "https://www.linkedin.com/in/two"},
                ],
            )
        return httpx.Response(
            200,
            json=[
                {"id": "p3", "linkedin_url_key": "https://www.linkedin.com/in/three"},
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SupabaseClient(
            "https://project.supabase.co",
            "service-secret",
            http=http,
            page_size=2,
        )
        result = await client.prospect_ids_by_linkedin_key()

    assert seen_offsets == ["0", "2"]
    assert result == {
        "https://www.linkedin.com/in/one": "p1",
        "https://www.linkedin.com/in/two": "p2",
        "https://www.linkedin.com/in/three": "p3",
    }


@pytest.mark.asyncio
async def test_event_insert_uses_database_conflict_key_and_returns_inserted_count():
    events = [
        {
            "prospect_id": "p1",
            "source": "LINKEDIN_MDP",
            "event_type": "LINKEDIN_CONNECTION_FOUND",
            "external_key": "connection:https://www.linkedin.com/in/one",
            "occurred_at": "2026-09-20T00:00:00+00:00",
            "payload": {"timestamp_semantics": "event_date_day_precision"},
        },
        {
            "prospect_id": "p2",
            "source": "LINKEDIN_MDP",
            "event_type": "LINKEDIN_CONNECTION_FOUND",
            "external_key": "connection:https://www.linkedin.com/in/two",
            "occurred_at": "2026-09-21T00:00:00+00:00",
            "payload": {"timestamp_semantics": "event_date_day_precision"},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "project.supabase.co"
        assert request.method == "POST"
        assert request.url.path == "/rest/v1/events"
        assert request.url.params["on_conflict"] == "source,external_key"
        assert request.headers["Prefer"] == "resolution=ignore-duplicates,return=representation"
        assert request.headers["apikey"] == "service-secret"
        assert request.headers["Authorization"] == "Bearer service-secret"
        return httpx.Response(201, json=[events[0]])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SupabaseClient("https://project.supabase.co", "service-secret", http=http)
        inserted = await client.insert_events_ignore_duplicates(events)

    assert inserted == 1


@pytest.mark.asyncio
async def test_redirect_cannot_forward_service_role_credentials_to_another_host():
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    ) as http:
        client = SupabaseClient("https://project.supabase.co", "service-secret", http=http)
        with pytest.raises(SupabaseAPIError):
            await client.prospect_ids_by_linkedin_key()

    assert seen_hosts == ["project.supabase.co"]


@pytest.mark.asyncio
async def test_supabase_error_does_not_expose_service_role_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "service-secret should not leak"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SupabaseClient("https://project.supabase.co", "service-secret", http=http)
        with pytest.raises(SupabaseAPIError) as exc:
            await client.prospect_ids_by_linkedin_key()

    assert "service-secret" not in str(exc.value)
