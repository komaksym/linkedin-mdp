"""Verify real private inbox persistence and exact-snapshot replay without data logs."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from linkedin_mdp_mcp.client import LinkedInMDPClient
from linkedin_mdp_mcp.inbox_sync import reconcile_inbox
from linkedin_mdp_mcp.supabase_client import SupabaseClient


class FrozenInbox:
    """Replay one real provider snapshot so new arrivals cannot change the oracle."""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        """Keep the fetched snapshot private and unchanged across both runs."""
        self.value = deepcopy(snapshot)

    async def snapshot(self, domain: str, **options: Any) -> dict[str, Any]:
        """Supply only the exact INBOX snapshot fetched for this validation."""
        assert domain == "INBOX"
        return deepcopy(self.value)


class VerifiedStore(SupabaseClient):
    """Use the production writer and privately verify the stored event evidence."""

    def __init__(self, base_url: str, service_role_key: str) -> None:
        """Initialize the production boundary and a private expected-event buffer."""
        super().__init__(base_url, service_role_key)
        self.expected: list[dict[str, Any]] = []

    async def insert_events_ignore_duplicates(
        self, events: Sequence[dict[str, Any]]
    ) -> int:
        """Capture the planned facts privately before calling the real database writer."""
        self.expected = deepcopy(list(events))
        existing = await self.read_events(self.expected)
        for event in self.expected:
            previous = existing.get(event["external_key"])
            if previous is not None:
                assert previous["payload"] == event["payload"]
                previous_time = datetime.fromisoformat(previous["occurred_at"])
                planned_time = datetime.fromisoformat(event["occurred_at"])
                if event["payload"]["timestamp_semantics"] == "actual":
                    assert previous_time == planned_time
                else:
                    assert (
                        previous_time.utcoffset() is not None
                        and previous_time <= planned_time
                    )
                event["occurred_at"] = previous["occurred_at"]
        return await super().insert_events_ignore_duplicates(events)

    async def read_events(
        self, events: Sequence[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Read private event bodies in bounded batches and reject duplicate keys."""
        result: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(events), 20):
            batch = events[offset : offset + 20]
            response = await self._request(
                "GET",
                "/rest/v1/events",
                params={
                    "source": "eq.LINKEDIN_MDP",
                    "external_key": "in.("
                    + ",".join(event["external_key"] for event in batch)
                    + ")",
                    "select": "prospect_id,source,event_type,external_key,occurred_at,payload",
                    "limit": "20",
                },
            )
            stored = self._json_list(response)
            by_key = {event["external_key"]: event for event in stored}
            assert len(by_key) == len(stored)
            assert set(by_key) <= {event["external_key"] for event in batch}
            result.update(by_key)
        return result

    async def verify_readback(self) -> None:
        """Compare every persisted payload and timestamp with its private write oracle."""
        assert self.expected
        stored = await self.read_events(self.expected)
        assert set(stored) == {event["external_key"] for event in self.expected}
        for expected in self.expected:
            actual = stored[expected["external_key"]]
            for field in (
                "prospect_id",
                "source",
                "event_type",
                "external_key",
                "payload",
            ):
                assert actual[field] == expected[field]
            assert datetime.fromisoformat(
                actual["occurred_at"]
            ) == datetime.fromisoformat(expected["occurred_at"])


async def verify() -> None:
    """Derive the account from complete invitation evidence and verify inbox replay."""
    linkedin = LinkedInMDPClient.from_env()
    configured_store = SupabaseClient.from_env()
    store = VerifiedStore(configured_store._base_url, configured_store._key)
    await configured_store.aclose()
    try:
        invitations = await linkedin.snapshot(
            "INVITATIONS", max_pages=50, strict_elements=True
        )
        assert invitations["truncated"] is False and invitations["page_count"] > 0
        assert all(
            isinstance(element, dict)
            and element.get("snapshotDomain") == "INVITATIONS"
            and isinstance(element.get("snapshotData"), list)
            for element in invitations["raw_elements"]
        )
        outgoing = [
            row
            for row in invitations["rows"]
            if isinstance(row, dict) and row.get("Direction") == "OUTGOING"
        ]
        assert outgoing and all(
            isinstance(row.get("inviterProfileUrl"), str) for row in outgoing
        )
        accounts = {row["inviterProfileUrl"].strip().rstrip("/") for row in outgoing}
        assert len(accounts) == 1
        account = accounts.pop()
        snapshot = await linkedin.snapshot("INBOX", max_pages=50, strict_elements=True)
        observed = datetime.now(UTC)
        frozen = FrozenInbox(snapshot)
        await reconcile_inbox(
            frozen,
            store,
            account_profile_url=account,
            observed_at=observed,
            dry_run=False,
        )
        first_keys = {event["external_key"] for event in store.expected}
        await store.verify_readback()
        second = await reconcile_inbox(
            frozen,
            store,
            account_profile_url=account,
            observed_at=observed,
            dry_run=False,
        )
        assert second.inserted == 0
        assert {event["external_key"] for event in store.expected} == first_keys
        await store.verify_readback()
    finally:
        try:
            await linkedin.aclose()
        finally:
            await store.aclose()


def main() -> int:
    """Emit only fixed verdicts and keep all exceptions and provider data private."""
    try:
        asyncio.run(verify())
    except Exception:  # noqa: BLE001
        print("live inbox evidence: verification failed")
        return 1
    print("live inbox evidence: persistence and duplicate-free replay verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
