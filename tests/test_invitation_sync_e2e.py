"""HTTP-boundary invitation reconciliation checks with privacy-safe synthetic data."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import httpx
import pytest

from linkedin_mdp_mcp.client import LinkedInAPIError, LinkedInMDPClient
from linkedin_mdp_mcp.invitation_sync import InvitationSyncError, reconcile_invitations
from linkedin_mdp_mcp.supabase_client import SupabaseClient

OBSERVED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
PROFILE = "https://www.linkedin.com/in/example-one"


def invitation(
    url: str = PROFILE, sent_at: Any = "9/17/26, 5:16 AM", direction: str = "OUTGOING"
) -> dict[str, Any]:
    """Create a synthetic provider row using verified export field names."""
    return {
        "Direction": direction,
        "inviteeProfileUrl": url,
        "Sent At": sent_at,
        "Message": "synthetic",
    }


def page(
    rows: list[Any], *, next_page: bool = False, elements: Any = None
) -> dict[str, Any]:
    """Create a provider page with optional continuation and malformed elements."""
    data = (
        [{"snapshotDomain": "INVITATIONS", "snapshotData": rows}]
        if elements is None
        else elements
    )
    links = (
        [{"rel": "next", "href": "/rest/memberSnapshotData?start=1"}]
        if next_page
        else []
    )
    return {"elements": data, "paging": {"links": links}}


class Boundary:
    """Hold synthetic HTTP state and record every request at both service boundaries."""

    def __init__(
        self,
        pages: list[dict[str, Any] | int],
        prospects: list[dict[str, str]] | None = None,
    ) -> None:
        """Initialize pages, existing prospects, and a durable event-key set."""
        self.pages = pages
        self.prospects = (
            prospects
            if prospects is not None
            else [{"id": "p1", "linkedin_url_key": PROFILE}]
        )
        self.requests: list[tuple[str, str]] = []
        self.events: dict[str, dict[str, Any]] = {}

    def respond(self, request: httpx.Request) -> httpx.Response:
        """Return linked pages and emulate Supabase's ignore-duplicates writer."""
        self.requests.append((request.url.host, request.method))
        if request.url.host == "api.linkedin.com":
            index = 1 if request.url.params.get("start") == "1" else 0
            result = self.pages[index]
            if isinstance(result, int):
                return httpx.Response(result, json={"message": "synthetic failure"})
            return httpx.Response(200, json=result)
        if request.url.path == "/rest/v1/prospects":
            return httpx.Response(200, json=self.prospects)
        assert request.url.path == "/rest/v1/events"
        assert request.url.params.get("on_conflict") == "source,external_key"
        assert (
            request.headers["Prefer"]
            == "resolution=ignore-duplicates,return=representation"
        )
        import json

        inserted = []
        for event in json.loads(request.content):
            if event["external_key"] not in self.events:
                self.events[event["external_key"]] = event
                inserted.append(event)
        return httpx.Response(201, json=inserted)

    async def run(self) -> Any:
        """Run the production orchestrator with real clients and a mock transport."""
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(self.respond)
        ) as http:
            linkedin = LinkedInMDPClient("synthetic-token", http=http, max_retries=0)
            supabase = SupabaseClient(
                "https://database.example", "synthetic-key", http=http
            )
            return await reconcile_invitations(
                linkedin, supabase, observed_at=OBSERVED_AT
            )


@pytest.mark.asyncio
async def test_complete_multipage_history_is_matched_deduped_and_idempotent() -> None:
    """Prove canonical identity, provider-time keys, row counts, and immediate rerun."""
    boundary = Boundary(
        [
            page(
                [
                    invitation(" http://uk.linkedin.com/in/example-one/?trk=x "),
                    invitation(direction="INCOMING"),
                    invitation(direction="UNKNOWN"),
                    invitation("https://www.linkedin.com/in/unknown"),
                    invitation(sent_at="bad time"),
                    invitation(sent_at=None),
                ],
                next_page=True,
            ),
            page(
                [
                    invitation(
                        "https://linkedin.com/in/example-one/", "2026-09-17T05:16:00"
                    ),
                    invitation(sent_at="9/18/26, 6:00 AM"),
                    invitation(sent_at="2026-09-19T06:00:00+02:00"),
                ]
            ),
        ]
    )

    first = await boundary.run()
    second = await boundary.run()

    assert first.fetched_rows == 9
    assert first.inbound_rows == 1
    assert first.unmatched_rows == 1
    assert first.malformed_rows == 3
    assert first.matched_rows == 4
    assert first.unique_matched == 3
    assert first.inserted == 3
    assert first.already_present == 0
    assert second.inserted == 0
    assert second.already_present == 3
    assert len(boundary.events) == 3
    assert all(
        key.startswith("invitation-history:https://www.linkedin.com/in/example-one:")
        for key in boundary.events
    )
    naive = boundary.events[
        "invitation-history:https://www.linkedin.com/in/example-one:2026-09-17T05:16:00"
    ]
    assert naive["occurred_at"] == OBSERVED_AT.isoformat()
    assert naive["event_type"] == "LINKEDIN_INVITATION_HISTORY_FOUND"
    assert naive["payload"]["timestamp_semantics"] == "observed_at"
    assert (
        naive["payload"]["provider_timestamp_semantics"]
        == "local_time_timezone_unspecified"
    )
    assert naive["payload"]["provider_timestamp"] == "9/17/26, 5:16 AM"
    assert naive["payload"]["canonical_url"] == PROFILE
    assert naive["payload"]["observed_via"] == "INVITATIONS"
    assert naive["payload"]["lifecycle_state"] == "unknown"
    assert naive["payload"]["raw"]["Message"] == "synthetic"
    aware = boundary.events[
        "invitation-history:https://www.linkedin.com/in/example-one:2026-09-19T04:00:00+00:00"
    ]
    assert aware["occurred_at"] == "2026-09-19T04:00:00+00:00"
    assert aware["payload"]["timestamp_semantics"] == "actual"
    assert aware["payload"]["provider_timestamp_semantics"] == "absolute_time"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pages", "error"),
    [
        ([404], InvitationSyncError),
        ([page([], next_page=True), 403], LinkedInAPIError),
        (
            [
                {
                    "elements": [
                        {
                            "snapshotDomain": "INVITATIONS",
                            "snapshotData": [invitation()],
                        },
                        {"snapshotDomain": "INVITATIONS", "snapshotData": []},
                    ],
                    "paging": {
                        "links": [
                            {"rel": "next", "href": "/rest/memberSnapshotData?start=1"}
                        ]
                    },
                },
                {},
            ],
            LinkedInAPIError,
        ),
        (
            [
                {
                    "elements": [
                        {
                            "snapshotDomain": "INVITATIONS",
                            "snapshotData": [invitation()],
                        },
                        {"snapshotDomain": "INVITATIONS", "snapshotData": []},
                    ],
                    "paging": {
                        "links": [
                            {"rel": "next", "href": "/rest/memberSnapshotData?start=1"}
                        ]
                    },
                },
                {"elements": {}, "paging": {"links": []}},
            ],
            LinkedInAPIError,
        ),
        ([page([], elements=["bad"])], InvitationSyncError),
        ([page([], elements=[{"snapshotDomain": "INVITATIONS"}])], InvitationSyncError),
        (
            [
                page(
                    [], elements=[{"snapshotDomain": "CONNECTIONS", "snapshotData": []}]
                )
            ],
            InvitationSyncError,
        ),
        (
            [
                page(
                    [], elements=[{"snapshotDomain": "INVITATIONS", "snapshotData": {}}]
                )
            ],
            InvitationSyncError,
        ),
        ([{"paging": {"links": []}}], LinkedInAPIError),
        ([{"elements": [], "paging": []}], LinkedInAPIError),
        ([{"elements": [], "paging": {"links": {}}}], LinkedInAPIError),
        ([{"elements": [], "paging": {"links": [{"rel": "next"}]}}], LinkedInAPIError),
        ([{"elements": [], "paging": {"links": [{"href": "/rest/memberSnapshotData?start=1"}]}}], LinkedInAPIError),
        ([{"elements": [], "paging": {"links": [{"rel": None, "href": "/rest/memberSnapshotData?start=1"}]}}], LinkedInAPIError),
        (
            [
                {
                    "elements": [
                        {
                            "snapshotDomain": "INVITATIONS",
                            "snapshotData": [invitation()],
                        }
                    ],
                    "paging": {"start": 0, "count": 1, "total": 2, "links": []},
                }
            ],
            LinkedInAPIError,
        ),
    ],
)
async def test_incomplete_or_malformed_history_never_reaches_database(
    pages: list[Any], error: type[Exception]
) -> None:
    """Reject unavailable, later-page failure, and malformed raw envelopes before reads or writes."""
    boundary = Boundary(pages)
    with pytest.raises(error):
        await boundary.run()
    assert boundary.requests
    assert {host for host, _ in boundary.requests} == {"api.linkedin.com"}
    assert boundary.events == {}


@pytest.mark.asyncio
async def test_truncation_never_reaches_database() -> None:
    """Exhaust the 50-page safety limit while LinkedIn still advertises a next page."""
    boundary = Boundary(
        [page([invitation()], next_page=True), page([invitation()], next_page=True)]
    )
    with pytest.raises(InvitationSyncError, match="truncated"):
        await boundary.run()
    assert len(boundary.requests) == 50
    assert {host for host, _ in boundary.requests} == {"api.linkedin.com"}


@pytest.mark.asyncio
async def test_empty_complete_page_is_safe_and_does_not_write() -> None:
    """Distinguish a successful empty domain page from a swallowed 404."""
    boundary = Boundary([page([])])
    result = await boundary.run()
    assert result.fetched_rows == 0
    assert result.inserted == 0
    assert boundary.events == {}
    assert boundary.requests == [
        ("api.linkedin.com", "GET"),
        ("database.example", "GET"),
    ]


@pytest.mark.asyncio
async def test_complete_page_with_no_elements_is_safe() -> None:
    """Accept an explicit empty elements list as a complete provider page."""
    boundary = Boundary([{"elements": [], "paging": {"links": []}}])
    result = await boundary.run()
    assert result.fetched_rows == 0
    assert result.inserted == 0
    assert boundary.events == {}


def test_manual_command_reports_only_safe_aggregate_error() -> None:
    """Exercise the command process without credentials and reject traceback output."""
    root = Path(__file__).resolve().parents[1]
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(root / "src")}
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "sync_invitations.py")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.strip() == '{"error": "invitation reconciliation failed"}'
    assert "Traceback" not in result.stderr
