from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from linkedin_mdp_mcp.connection_sync import (
    ConnectionSyncError,
    normalize_linkedin_url,
    plan_connection_events,
    reconcile_connections,
)


def test_reconciliation_plan_matches_existing_prospects_and_preserves_provider_evidence():
    observed_at = datetime(2026, 9, 24, 0, 30, tzinfo=timezone.utc)
    rows = [
        {
            "First Name": "Ada",
            "Last Name": "Lovelace",
            "URL": " http://uk.linkedin.com/in/ada-lovelace/?trk=abc#bio ",
            "Email Address": "",
            "Company": "Analytical Engines",
            "Position": "Engineer",
            "Connected On": "2026-09-20",
        },
        {
            "First Name": "Unknown",
            "Last Name": "Member",
            "URL": "https://www.linkedin.com/in/not-in-crm",
            "Email Address": "",
            "Company": "",
            "Position": "",
            "Connected On": "2026-09-21",
        },
    ]
    prospects_by_key = {
        "https://www.linkedin.com/in/ada-lovelace": "prospect-1",
    }

    plan = plan_connection_events(rows, prospects_by_key, observed_at=observed_at)

    assert plan.fetched == 2
    assert plan.matched == 1
    assert plan.unmatched == 1
    assert plan.events == [
        {
            "prospect_id": "prospect-1",
            "source": "LINKEDIN_MDP",
            "event_type": "LINKEDIN_CONNECTION_FOUND",
            "external_key": "connection:https://www.linkedin.com/in/ada-lovelace",
            "occurred_at": "2026-09-20T00:00:00+00:00",
            "payload": {
                "raw": rows[0],
                "timestamp_semantics": "event_date_day_precision",
            },
        }
    ]


def test_bad_connection_date_uses_observation_time_without_inventing_precision():
    observed_at = datetime(2026, 9, 24, 0, 30, 12, tzinfo=timezone.utc)
    row = {
        "URL": "https://linkedin.com/in/ada-lovelace/",
        "Connected On": "not-a-date",
    }

    plan = plan_connection_events(
        [row],
        {"https://www.linkedin.com/in/ada-lovelace": "prospect-1"},
        observed_at=observed_at,
    )

    assert plan.events[0]["occurred_at"] == "2026-09-24T00:30:12+00:00"
    assert plan.events[0]["payload"]["timestamp_semantics"] == "observed_at"


def test_missing_or_non_linkedin_url_is_unmatched():
    observed_at = datetime(2026, 9, 24, tzinfo=timezone.utc)

    plan = plan_connection_events(
        [
            {"Connected On": "2026-09-20"},
            {"URL": "https://example.com/in/not-linkedin", "Connected On": "2026-09-20"},
        ],
        {},
        observed_at=observed_at,
    )

    assert plan.fetched == 2
    assert plan.matched == 0
    assert plan.unmatched == 2
    assert plan.events == []


def test_python_normalizer_matches_database_identity_semantics():
    assert (
        normalize_linkedin_url(" HTTP://DE.LINKEDIN.COM/in/Ada/?x=1#bio ")
        == "https://www.linkedin.com/in/Ada"
    )
    assert (
        normalize_linkedin_url("https://www.linkedin.com/in/ada///")
        == "https://www.linkedin.com/in/ada"
    )
    assert normalize_linkedin_url("") is None
    assert normalize_linkedin_url("https://example.com/in/ada") is None


class FakeLinkedIn:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.result = snapshot
        self.calls: list[tuple[str, int]] = []

    async def snapshot(self, domain: str, *, max_pages: int = 10) -> dict[str, Any]:
        self.calls.append((domain, max_pages))
        return self.result


class FakeSupabase:
    def __init__(
        self,
        prospects: dict[str, str],
        *,
        inserted: int,
    ) -> None:
        self.prospects = prospects
        self.inserted = inserted
        self.written_events: list[dict[str, Any]] | None = None
        self.lookup_called = False

    async def prospect_ids_by_linkedin_key(self) -> dict[str, str]:
        self.lookup_called = True
        return self.prospects

    async def insert_events_ignore_duplicates(
        self,
        events: list[dict[str, Any]],
    ) -> int:
        self.written_events = events
        return self.inserted


@pytest.mark.asyncio
async def test_reconcile_connections_reports_inserted_and_already_present_counts():
    rows = [
        {"URL": "https://linkedin.com/in/one", "Connected On": "2026-09-20"},
        {"URL": "https://linkedin.com/in/two", "Connected On": "2026-09-21"},
        {"URL": "https://linkedin.com/in/unmatched", "Connected On": "2026-09-22"},
    ]
    linkedin = FakeLinkedIn({"rows": rows, "truncated": False})
    supabase = FakeSupabase(
        {
            "https://www.linkedin.com/in/one": "p1",
            "https://www.linkedin.com/in/two": "p2",
        },
        inserted=1,
    )

    summary = await reconcile_connections(
        linkedin,
        supabase,
        observed_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
    )

    assert linkedin.calls == [("CONNECTIONS", 50)]
    assert summary.fetched == 3
    assert summary.matched == 2
    assert summary.unmatched == 1
    assert summary.inserted == 1
    assert summary.already_present == 1
    assert supabase.written_events is not None
    assert len(supabase.written_events) == 2


@pytest.mark.asyncio
async def test_reconcile_connections_fails_closed_on_truncated_snapshot():
    linkedin = FakeLinkedIn({"rows": [], "truncated": True})
    supabase = FakeSupabase({}, inserted=0)

    with pytest.raises(ConnectionSyncError, match="truncated"):
        await reconcile_connections(
            linkedin,
            supabase,
            observed_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        )

    assert supabase.lookup_called is False
    assert supabase.written_events is None
