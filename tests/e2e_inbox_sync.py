from __future__ import annotations

import asyncio
import importlib.util
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

from linkedin_mdp_mcp.client import LinkedInAPIError, LinkedInMDPClient
from linkedin_mdp_mcp.inbox_sync import InboxSyncError, reconcile_inbox
from linkedin_mdp_mcp.supabase_client import SupabaseAPIError, SupabaseClient

ACCOUNT = "https://www.linkedin.com/in/account"
PEER = "https://www.linkedin.com/in/prospect"
OBSERVED = datetime(2026, 9, 30, 12, tzinfo=UTC)


def message(**changes: Any) -> dict[str, Any]:
    """Return one complete provider inbox message row for synthetic scenarios."""
    row = {
        "CONVERSATION ID": "conversation-1",
        "DATE": "2026-09-30T10:00:00Z",
        "SENDER PROFILE URL": PEER,
        "RECIPIENT PROFILE URLS": [ACCOUNT],
        "CONTENT": "Hello",
        "FROM": "Same Name",
        "TO": "Same Name",
        "SUBJECT": "Intro",
        "FOLDER": "INBOX",
    }
    row.update(changes)
    return row


def response(status: int, body: Any) -> httpx.Response:
    """Build a JSON HTTP response for the synthetic provider and store."""
    return httpx.Response(status, json=body)


async def scenario(
    rows: list[Any],
    *,
    apply: bool = False,
    pages: list[Any] | None = None,
    store_state: dict[str, dict[str, Any]] | None = None,
    store_failure: bool = False,
    prospects: list[dict[str, str]] | None = None,
) -> tuple[Any, list[Any], list[dict[str, Any]]]:
    """Run reconciliation through real HTTP clients and capture store effects."""
    calls: list[Any] = []
    stored: list[dict[str, Any]] = []
    persistent = store_state if store_state is not None else {}
    provider_pages = pages or [{"elements": [{"snapshotDomain": "INBOX", "snapshotData": rows}]}]

    def linkedin_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        index = len(calls) - 1
        page = provider_pages[index]
        if isinstance(page, tuple):
            return response(page[0], page[1])
        return response(200, page)

    def store_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return response(
                200,
                prospects
                if prospects is not None
                else [{"id": "p1", "linkedin_url_key": "https://www.linkedin.com/in/prospect"}],
            )
        if store_failure:
            return response(500, {"message": "private synthetic failure body"})
        for event in json.loads(request.content):
            key = event["source"] + ":" + event["external_key"]
            if key not in persistent:
                persistent[key] = event
                stored.append(event)
        return response(201, stored)

    linkedin = LinkedInMDPClient("synthetic", http=httpx.AsyncClient(transport=httpx.MockTransport(linkedin_handler)))
    supabase = SupabaseClient(
        "https://db.example.test", "synthetic", http=httpx.AsyncClient(transport=httpx.MockTransport(store_handler))
    )
    try:
        result = await reconcile_inbox(
            linkedin,
            supabase,
            account_profile_url=ACCOUNT,
            observed_at=OBSERVED,
            dry_run=not apply,
        )
    finally:
        await linkedin.aclose()
        await supabase.aclose()
    return result, calls, stored


async def main() -> None:
    """Run deterministic HTTP-boundary inbox scenarios and save safe verdicts."""
    verdicts: dict[str, bool] = {}

    result, _, stored = await scenario([message()], apply=True)
    event = stored[0]
    verdicts["inbound_persisted"] = (
        event["event_type"] == "LINKEDIN_MESSAGE_OBSERVED"
        and event["payload"]["direction"] == "inbound"
        and event["payload"]["read_state"] == "unknown"
        and event["payload"]["content"] == "Hello"
    )
    verdicts["writes_are_opt_in"] = result.inserted == 1
    verdicts["aware_timestamp_is_actual"] = (
        event["occurred_at"] == "2026-09-30T10:00:00+00:00"
        and event["payload"]["timestamp_semantics"] == "actual"
    )

    utc_row = message(DATE="2026-09-30 10:00:00 UTC")
    utc_state: dict[str, dict[str, Any]] = {}
    utc_first, _, utc_stored = await scenario([utc_row], apply=True, store_state=utc_state)
    verdicts["provider_utc_suffix_persisted"] = (
        utc_first.planned_events == 1
        and len(utc_stored) == 1
        and utc_stored[0]["occurred_at"] == "2026-09-30T10:00:00+00:00"
        and utc_stored[0]["payload"]["timestamp_semantics"] == "actual"
        and utc_stored[0]["payload"]["provider_timestamp"] == utc_row["DATE"]
        and "provider_local_time_timezone_unspecified" not in utc_stored[0]["payload"]
    )
    utc_second, _, utc_replay = await scenario([utc_row], apply=True, store_state=utc_state)
    verdicts["provider_utc_suffix_replay_is_idempotent"] = (
        utc_first.inserted == 1 and utc_second.inserted == 0 and not utc_replay
    )

    for value in (
        "2026-09-30 10:00:00 PST",
        "2026-09-30 10:00:00 XYZ",
        "2026-09-30 UTC",
        "2026-09-30 10:00:00+02:00 UTC",
        "2026-02-30 10:00:00 UTC",
    ):
        invalid_time, _, invalid_write = await scenario([message(DATE=value)], apply=True)
        if invalid_time.planned_events or invalid_write:
            raise AssertionError("ambiguous or invalid suffixed provider time was accepted")
    verdicts["ambiguous_suffixed_timestamps_rejected"] = True

    provider_string_shape = message(**{"RECIPIENT PROFILE URLS": ACCOUNT})
    provider_string_result, _, provider_string_stored = await scenario([provider_string_shape], apply=True)
    verdicts["provider_string_recipients_supported"] = (
        provider_string_result.planned_events == 1
        and provider_string_stored[0]["payload"]["direction"] == "inbound"
    )

    state: dict[str, dict[str, Any]] = {}
    replay_first, _, _ = await scenario([message()], apply=True, store_state=state)
    replay_second, _, replay_writes = await scenario([message()], apply=True, store_state=state)
    verdicts["database_replay_is_idempotent"] = (
        replay_first.inserted == 1
        and replay_second.inserted == 0
        and replay_second.already_present == 1
        and not replay_writes
        and len(state) == 1
    )

    outbound = message(**{"SENDER PROFILE URL": ACCOUNT, "RECIPIENT PROFILE URLS": [PEER]})
    _, _, outbound_stored = await scenario([outbound], apply=True)
    verdicts["outbound_persisted"] = outbound_stored[0]["payload"]["direction"] == "outbound"

    regional = message(**{"SENDER PROFILE URL": "http://uk.linkedin.com/in/prospect/?trk=export"})
    _, _, regional_stored = await scenario([regional], apply=True)
    verdicts["regional_profile_canonicalized"] = (
        regional_stored[0]["payload"]["sender_profile_url"] == "https://www.linkedin.com/in/prospect"
    )

    alias_rows = [
        {"id": "p1", "linkedin_url_key": "http://uk.linkedin.com/in/prospect/"},
        {"id": "p2", "linkedin_url_key": "https://www.linkedin.com/in/prospect"},
    ]
    alias_result, _, alias_stored = await scenario([message()], apply=True, prospects=alias_rows)
    verdicts["prospect_alias_collision_skipped"] = (
        alias_result.planned_events == 0 and alias_result.skip_reasons.get("unknown_prospect") == 1 and not alias_stored
    )

    duplicate, _, _ = await scenario([message(), message()], apply=True)
    verdicts["exact_rows_deduplicated"] = duplicate.planned_events == 1

    changed_content = message(CONTENT="Edited message")
    changed, _, changed_stored = await scenario([message(), changed_content], apply=True)
    verdicts["content_versions_keep_distinct_identity"] = changed.planned_events == 2 and len(changed_stored) == 2

    changed_subject, _, subject_stored = await scenario(
        [message(), message(SUBJECT="Different subject")], apply=True
    )
    verdicts["subject_variants_keep_distinct_identity"] = (
        changed_subject.planned_events == 2 and len(subject_stored) == 2
    )

    naive = message(DATE="2026-09-30 10:00:00")
    _, _, naive_stored = await scenario([naive], apply=True)
    verdicts["naive_time_keeps_timezone_unspecified"] = (
        naive_stored[0]["payload"]["timestamp_semantics"] == "observed_at"
        and naive_stored[0]["payload"]["provider_local_time_timezone_unspecified"] is True
    )

    attachment_only = message(CONTENT="", ATTACHMENTS="image-token")
    _, _, attachment_stored = await scenario([attachment_only], apply=True)
    verdicts["attachment_only_content_is_preserved"] = (
        attachment_stored[0]["payload"]["content"] == ""
        and attachment_stored[0]["payload"]["attachments"] == {"ATTACHMENTS": "image-token"}
    )

    empty_attachment = message(CONTENT="", ATTACHMENTS="")
    empty_attachment_result, _, empty_attachment_stored = await scenario([empty_attachment], apply=True)
    verdicts["empty_attachment_does_not_create_message"] = (
        empty_attachment_result.planned_events == 0 and not empty_attachment_stored
    )

    group = [message(), message(**{"RECIPIENT PROFILE URLS": [PEER, "https://www.linkedin.com/in/third"]})]
    group_result, _, group_stored = await scenario(group, apply=True)
    verdicts["group_conversation_withheld_whole"] = group_result.planned_events == 0 and not group_stored

    malformed_peer = message(**{"RECIPIENT PROFILE URLS": "https://www.linkedin.com/in/third"})
    malformed_result, _, malformed_stored = await scenario([message(), malformed_peer], apply=True)
    verdicts["malformed_participant_taints_thread"] = (
        malformed_result.planned_events == 0 and not malformed_stored and malformed_result.withheld_thread_rows >= 2
    )

    mixed_multi = [
        message(),
        message(**{"SENDER PROFILE URL": PEER, "RECIPIENT PROFILE URLS": [ACCOUNT, PEER]}),
    ]
    mixed_multi_result, _, mixed_multi_stored = await scenario(mixed_multi, apply=True)
    verdicts["multiple_recipient_taints_entire_thread"] = (
        mixed_multi_result.planned_events == 0
        and mixed_multi_result.withheld_thread_rows == 2
        and not mixed_multi_stored
    )

    mixed_self = [
        message(),
        message(**{"SENDER PROFILE URL": PEER, "RECIPIENT PROFILE URLS": [PEER]}),
    ]
    mixed_self_result, _, mixed_self_stored = await scenario(mixed_self, apply=True)
    verdicts["self_message_taints_entire_thread"] = (
        mixed_self_result.planned_events == 0
        and mixed_self_result.withheld_thread_rows == 2
        and not mixed_self_stored
    )

    dry, _, dry_stored = await scenario([message()])
    verdicts["dry_run_writes_nothing"] = dry.planned_events == 1 and not dry_stored

    try:
        await scenario([message()], apply=True, store_failure=True)
    except SupabaseAPIError:
        verdicts["store_failure_is_reported"] = True
    else:
        verdicts["store_failure_is_reported"] = False

    malformed_dates = ["", "2026-09-30", "not a date"]
    for value in malformed_dates:
        malformed_result, _, malformed_write = await scenario([message(DATE=value)], apply=True)
        if malformed_result.planned_events != 0 or malformed_result.skip_reasons.get("malformed_timestamp") != 1 or malformed_write:
            raise AssertionError("malformed or date-only provider time was accepted")
    verdicts["malformed_timestamps_rejected"] = True

    invalid_urls = [
        "https://linkedin.com.example.org/in/prospect",
        "https://user@linkedin.com/in/prospect",
        "https://www.linkedin.com/in/prospect/settings",
        "https://www.linkedin.com/company/prospect",
    ]
    for value in invalid_urls:
        bad_url_result, _, bad_url_write = await scenario([message(**{"SENDER PROFILE URL": value})], apply=True)
        if bad_url_result.planned_events != 0 or bad_url_write:
            raise AssertionError("unsupported or unsafe profile URL was accepted")
    verdicts["strict_profile_identity"] = True

    unknown_peer = message(**{"SENDER PROFILE URL": "https://www.linkedin.com/in/unknown"})
    unknown, _, unknown_stored = await scenario([unknown_peer], apply=True)
    verdicts["unknown_peer_skipped"] = unknown.planned_events == 0 and not unknown_stored

    self_message = message(**{"SENDER PROFILE URL": ACCOUNT, "RECIPIENT PROFILE URLS": [ACCOUNT]})
    self_result, _, self_stored = await scenario([self_message], apply=True)
    verdicts["self_message_skipped"] = self_result.planned_events == 0 and not self_stored

    try:
        await scenario([message()], pages=[{"elements": ["malformed"]}])
    except InboxSyncError:
        verdicts["malformed_snapshot_fails_closed"] = True
    else:
        verdicts["malformed_snapshot_fails_closed"] = False

    for bad in [
        {"elements": [{"snapshotDomain": "CONNECTIONS", "snapshotData": [message()]}]},
        {"elements": [{"snapshotDomain": "INBOX", "snapshotData": [message()]}], "paging": {"links": "bad"}},
        {"elements": [{"snapshotDomain": "INBOX", "snapshotData": [message()]}], "source_result": "not_found"},
        {"elements": [{"snapshotDomain": "INBOX", "snapshotData": [message()]}], "page_count": True},
        {"elements": [{"snapshotDomain": "INBOX", "snapshotData": [message()]}], "paging": "bad"},
        {"elements": [{"snapshotDomain": "INBOX", "snapshotData": [message()]}], "paging": {"links": [{"rel": "next"}]}},
        {"elements": [{"snapshotDomain": "INBOX", "snapshotData": [message()]}], "paging": {"links": [{"rel": "next", "href": "https://evil.example/steal"}]}},
    ]:
        try:
            await scenario([message()], pages=[bad])
        except (InboxSyncError, LinkedInAPIError):
            pass
        else:
            raise AssertionError("invalid snapshot metadata was accepted")
    verdicts["snapshot_metadata_rejected"] = True

    scope_rejected = True
    for href in (
        "/rest/memberChangeLogs?start=2",
        "/rest/memberSnapshotData?domain=CONNECTIONS&start=2",
        "/rest/memberSnapshotData?domain=INBOX&domain=CONNECTIONS&start=2",
        "/rest/memberSnapshotData?q=all&domain=INBOX&start=2",
    ):
        scope_state: dict[str, dict[str, Any]] = {}
        try:
            await scenario(
                [message()],
                apply=True,
                store_state=scope_state,
                pages=[
                    {
                        "elements": [{"snapshotDomain": "INBOX", "snapshotData": [message()]}],
                        "paging": {"links": [{"rel": "next", "href": href}]},
                    },
                    {"elements": []},
                ],
            )
        except (InboxSyncError, LinkedInAPIError):
            scope_rejected = scope_rejected and not scope_state
        else:
            scope_rejected = False
    verdicts["snapshot_pagination_scope_cannot_change"] = scope_rejected

    scoped, scoped_calls, scoped_writes = await scenario(
        [message()],
        apply=True,
        pages=[
            {
                "elements": [{"snapshotDomain": "INBOX", "snapshotData": [message()]}],
                "paging": {"links": [{"rel": "next", "href": "/rest/memberSnapshotData?start=2"}]},
            },
            {"elements": []},
        ],
    )
    verdicts["partial_next_link_retains_snapshot_scope"] = (
        scoped.planned_events == 1
        and len(scoped_writes) == 1
        and scoped_calls[1].url.params.get("domain") == "INBOX"
        and scoped_calls[1].url.params.get("q") == "criteria"
        and scoped_calls[1].url.params.get("start") == "2"
    )

    second_page = {"elements": [{"snapshotDomain": "INBOX", "snapshotData": [message()]}]}
    for links in [
        [
            {"rel": "next", "href": "/rest/memberSnapshotData?start=2"},
            {"rel": "self", "href": "/rest/memberSnapshotData?start=1"},
            {"rel": "next", "href": "/rest/memberSnapshotData?start=3"},
        ],
        [
            {"rel": "next", "href": "/rest/memberSnapshotData?start=2"},
            {"rel": "self"},
        ],
    ]:
        try:
            await scenario(
                [],
                pages=[
                    {
                        "elements": [{"snapshotDomain": "INBOX", "snapshotData": []}],
                        "paging": {"links": links},
                    },
                    second_page,
                ],
            )
        except InboxSyncError:
            pass
        else:
            raise AssertionError("ambiguous or malformed snapshot links were accepted")
    verdicts["all_snapshot_links_validated"] = True

    class IncompleteSnapshotClient:
        """Return a malformed direct snapshot envelope for the reconcile boundary."""

        async def snapshot(self, domain: str, *, max_pages: int, strict_elements: bool) -> dict[str, Any]:
            """Return one top-level unavailable result without raw elements."""
            return {
                "truncated": False,
                "page_count": 1,
                "rows": [],
                "raw_elements": [],
                "source_result": "unavailable",
            }

    class NoWriteStore:
        """Track store calls while rejecting an unavailable snapshot."""

        def __init__(self) -> None:
            """Initialize the store-call counter."""
            self.lookup_calls = 0
            self.write_calls = 0

        async def prospect_ids_by_linkedin_key(self) -> dict[str, str]:
            """Count prospect lookups made before snapshot validation."""
            self.lookup_calls += 1
            return {}

        async def insert_events_ignore_duplicates(self, events: list[dict[str, Any]]) -> int:
            """Count event writes made before snapshot validation."""
            self.write_calls += 1
            return 0

    unavailable_store = NoWriteStore()
    try:
        await reconcile_inbox(
            IncompleteSnapshotClient(), unavailable_store, account_profile_url=ACCOUNT, observed_at=OBSERVED
        )
    except InboxSyncError:
        pass
    else:
        raise AssertionError("top-level unavailable source result was accepted")
    verdicts["top_level_source_result_rejected_before_store_access"] = (
        unavailable_store.lookup_calls == 0 and unavailable_store.write_calls == 0
    )

    try:
        await scenario([], pages=[(404, {"message": "not found"})])
    except InboxSyncError:
        verdicts["first_page_404_blocked"] = True
    else:
        verdicts["first_page_404_blocked"] = False

    try:
        await scenario([], pages=[{"elements": [{"snapshotDomain": "INBOX", "snapshotData": [message()]}], "paging": {"links": [{"rel": "next", "href": "/rest/memberSnapshotData?start=2"}]}}, (404, {"message": "other 404"})])
    except InboxSyncError:
        verdicts["generic_late_404_blocked"] = True
    else:
        verdicts["generic_late_404_blocked"] = False

    late = [{"elements": [{"snapshotDomain": "INBOX", "snapshotData": [message()]}], "paging": {"links": [{"rel": "next", "href": "/rest/memberSnapshotData?start=2"}]}}, (404, {"message": "No data found for this domain and memberId"})]
    late_result, _, _ = await scenario([], pages=late)
    verdicts["known_late_exhaustion_accepted"] = late_result.fetched_rows == 1

    capped = [
        {"elements": [{"snapshotDomain": "INBOX", "snapshotData": []}], "paging": {"links": [{"rel": "next", "href": f"/rest/memberSnapshotData?start={index + 1}"}]}}
        for index in range(50)
    ]
    capped_result = None
    try:
        capped_result, _, capped_stored = await scenario([], pages=capped)
    except InboxSyncError:
        capped_stored = []
    verdicts["page_cap_blocks_before_write"] = capped_result is None and not capped_stored

    cli_spec = importlib.util.spec_from_file_location("sync_inbox", "scripts/sync_inbox.py")
    if cli_spec is None or cli_spec.loader is None:
        raise AssertionError("CLI module could not be loaded")
    sync_inbox = importlib.util.module_from_spec(cli_spec)
    cli_spec.loader.exec_module(sync_inbox)

    class FakeClient:
        """Stand in for a configured HTTP client while checking CLI output."""

        @classmethod
        def from_env(cls) -> FakeClient:
            """Return a fake configured client without reading credentials."""
            return cls()

        async def aclose(self) -> None:
            """Close the fake client without side effects."""

    cli_capture = StringIO()
    with patch.object(sync_inbox, "LinkedInMDPClient", FakeClient), patch.object(
        sync_inbox, "SupabaseClient", FakeClient
    ), patch.object(sync_inbox, "reconcile_inbox", side_effect=RuntimeError("private synthetic exception body")), redirect_stdout(cli_capture):
        cli_code = await sync_inbox.run(False, ACCOUNT)
    verdicts["cli_failure_is_sanitized"] = cli_code == 1 and cli_capture.getvalue() == "FAILED\n"

    cli_capture = StringIO()
    with patch.object(sync_inbox, "LinkedInMDPClient", FakeClient), patch.object(
        sync_inbox, "SupabaseClient", FakeClient
    ), patch.object(sync_inbox, "reconcile_inbox") as reconcile, redirect_stdout(cli_capture):
        cli_code = await sync_inbox.run(False, ACCOUNT)
    verdicts["cli_defaults_to_dry_run"] = (
        cli_code == 0
        and cli_capture.getvalue() == "DRY_RUN_COMPLETE\n"
        and reconcile.await_args.kwargs["dry_run"] is True
    )

    cli_capture = StringIO()
    with patch.object(sync_inbox, "LinkedInMDPClient", FakeClient), patch.object(
        sync_inbox, "SupabaseClient", FakeClient
    ), patch.object(sync_inbox, "reconcile_inbox") as reconcile, redirect_stdout(cli_capture):
        cli_code = await sync_inbox.run(True, ACCOUNT)
    verdicts["cli_apply_is_explicit"] = (
        cli_code == 0
        and cli_capture.getvalue() == "APPLIED\n"
        and reconcile.await_args.kwargs["dry_run"] is False
    )

    closed_clients: list[str] = []

    class ClosingClient:
        """Record CLI cleanup attempts without opening network clients."""

        label = "linkedin"

        @classmethod
        def from_env(cls) -> ClosingClient:
            """Return a new cleanup tracker instance."""
            return cls()

        async def aclose(self) -> None:
            """Record cleanup for this fake client."""
            closed_clients.append(self.label)

    class FailingCloseClient(ClosingClient):
        """Fail the first cleanup while allowing the other client to close."""

        label = "supabase"

        async def aclose(self) -> None:
            """Record cleanup and raise a synthetic close error."""
            closed_clients.append(self.label)
            raise RuntimeError("private synthetic close body")

    cli_capture = StringIO()
    with patch.object(sync_inbox, "LinkedInMDPClient", ClosingClient), patch.object(
        sync_inbox, "SupabaseClient", FailingCloseClient
    ), patch.object(sync_inbox, "reconcile_inbox"), redirect_stdout(cli_capture):
        cli_code = await sync_inbox.run(False, ACCOUNT)
    verdicts["cleanup_attempts_both_clients"] = (
        cli_code == 1 and cli_capture.getvalue() == "FAILED\n" and closed_clients == ["supabase", "linkedin"]
    )

    process = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "HEAD", stdout=asyncio.subprocess.PIPE
    )
    output, _ = await process.communicate()
    if process.returncode != 0:
        raise AssertionError("source revision could not be read")
    revision = output.decode().strip()
    evidence = {"source_revision": f"{revision}+working-tree", "scenario_count": len(verdicts), "passed": all(verdicts.values()), "scenarios": verdicts}
    path = Path("artifacts/inbox-e2e-evidence.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, sort_keys=True))
    if not evidence["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
