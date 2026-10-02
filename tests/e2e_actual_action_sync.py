"""Exercise staged actual-action sync against synthetic provider and event-store HTTP."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import stat
import tempfile
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

from linkedin_mdp_mcp.actual_action_sync import (
    ActualActionSources,
    ActualSyncError,
    acquire_actual_action_sources,
    plan_verified_dm_events,
    sync_actual_actions,
)
from linkedin_mdp_mcp.client import LinkedInMDPClient
from linkedin_mdp_mcp.supabase_client import SupabaseClient
from scripts import sync_actual_actions as cli

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "https://www.linkedin.com/in/synthetic-owner"
PEER = "https://www.linkedin.com/in/synthetic-peer"
OTHER = "https://www.linkedin.com/in/synthetic-other"
OWNER_URN = "urn:li:person:synthetic-owner"
PEER_URN = "urn:li:person:synthetic-peer"


def message(*, inbound: bool = False, peer: str = PEER, thread: str = "thread-one", minutes: int = 20) -> dict[str, Any]:
    """Build a current one-to-one provider INBOX row."""
    sender, recipient = (peer, ACCOUNT) if inbound else (ACCOUNT, peer)
    return {"CONVERSATION ID": thread, "SENDER PROFILE URL": sender,
        "RECIPIENT PROFILE URLS": [recipient], "DATE": (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(),
        "CONTENT": "Synthetic message", "ATTACHMENTS": []}


def activity(row: dict[str, Any], *, inbound: bool = False, activity_id: str = "activity-one") -> dict[str, Any]:
    """Build a successful CREATE activity uniquely matching an INBOX row."""
    author = PEER_URN if inbound else OWNER_URN
    moment = datetime.fromisoformat(row["DATE"])
    return {"owner": OWNER_URN, "actor": author, "resourceName": "messages", "method": "CREATE",
        "activityStatus": "SUCCESS", "resourceId": activity_id, "activityId": "call-" + activity_id,
        "activity": {"id": activity_id, "owner": author, "author": author,
            "thread": "urn:li:messagingThread:" + row["CONVERSATION ID"],
            "createdAt": int(moment.timestamp() * 1000),
            "content": {"format": "TEXT", "formatVersion": 1, "fallback": row["CONTENT"]}, "attachments": []}}


class Services:
    """Serve actual HTTP client calls and persist events by the DB conflict key."""

    def __init__(self, *, inbox: list[dict[str, Any]] | None = None, changes: list[dict[str, Any]] | None = None, invitations: list[dict[str, Any]] | None = None, connections: list[dict[str, Any]] | None = None, prospects: list[dict[str, str]] | None = None, failure: str | None = None, state: dict[tuple[str, str], dict[str, Any]] | None = None) -> None:
        """Configure one synthetic source and a reusable event store."""
        outbound = message()
        inbound = message(inbound=True, minutes=10)
        self.inbox = deepcopy(inbox if inbox is not None else [outbound, inbound])
        self.changes = deepcopy(changes if changes is not None else [activity(outbound), activity(inbound, inbound=True, activity_id="activity-two")])
        self.failure = failure
        self.invitations = invitations if invitations is not None else [{"Direction": "OUTGOING", "inviteeProfileUrl": PEER, "Sent At": "10/01/26, 10:00 AM"}]
        self.connections = connections if connections is not None else [{"URL": PEER, "Connected On": "2026-10-01"}]
        self.prospects = prospects if prospects is not None else [{"id": "prospect-one", "linkedin_url_key": PEER}, {"id": "prospect-two", "linkedin_url_key": OTHER}]
        self.state = state if state is not None else {}
        self.provider_calls: list[httpx.Request] = []
        self.store_gets = 0
        self.store_posts = 0
        self.posted: list[list[dict[str, Any]]] = []

    def provider(self, request: httpx.Request) -> httpx.Response:
        """Return domain-specific real client envelopes or one injected failure."""
        self.provider_calls.append(request)
        if request.url.path.endswith("memberChangeLogs"):
            if self.failure == "changelog_404":
                return httpx.Response(404, json={"message": "synthetic unavailable"})
            if self.failure == "changelog_truncated":
                return httpx.Response(200, json={"elements": self.changes, "paging": {"links": [{"rel": "next", "href": "/rest/memberChangeLogs?start=next"}]}})
            return httpx.Response(200, content=json.dumps({"elements": self.changes}, ensure_ascii=True).encode("utf-8"))
        assert request.url.path.endswith("memberSnapshotData")
        domain = request.url.params["domain"]
        if self.failure == "invitations_404" and domain == "INVITATIONS":
            return httpx.Response(404, json={"message": "synthetic unavailable"})
        if self.failure == "malformed_inbox_envelope" and domain == "INBOX":
            return httpx.Response(200, json={"elements": [{"snapshotDomain": "INBOX", "snapshotData": {"bad": "shape"}}]})
        if self.failure == "malformed_connection_paging" and domain == "CONNECTIONS":
            return httpx.Response(200, json={"elements": [], "paging": {"links": "bad"}})
        if self.failure == "inbox_truncated" and domain == "INBOX":
            return httpx.Response(200, json={"elements": [{"snapshotDomain": "INBOX", "snapshotData": self.inbox}], "paging": {"links": [{"rel": "next", "href": "/rest/memberSnapshotData?start=next"}]}})
        rows = {"INVITATIONS": self.invitations, "CONNECTIONS": self.connections, "INBOX": self.inbox}[domain]
        return httpx.Response(200, content=json.dumps({"elements": [{"snapshotDomain": domain, "snapshotData": rows}]}, ensure_ascii=True).encode("utf-8"))

    def store(self, request: httpx.Request) -> httpx.Response:
        """Mimic one paged prospect read and unique(source, external_key) inserts."""
        if request.method == "GET":
            self.store_gets += 1
            prospects = list(self.prospects)
            if self.failure == "prospect_conflict":
                prospects.append({"id": "different", "linkedin_url_key": PEER})
            return httpx.Response(200, json=prospects)
        self.store_posts += 1
        assert request.url.params["on_conflict"] == "source,external_key"
        assert "resolution=ignore-duplicates" in request.headers["Prefer"]
        batch = json.loads(request.content)
        self.posted.append(batch)
        inserted = []
        for event in batch:
            key = (event["source"], event["external_key"])
            if key not in self.state:
                self.state[key] = event
                inserted.append(event)
        if self.failure == "timeout_after_commit":
            raise httpx.ReadTimeout("synthetic timeout")
        return httpx.Response(201, json=inserted)


async def run_service(service: Services, *, apply: bool = False, account: str = ACCOUNT, urn: str = OWNER_URN, sources: ActualActionSources | None = None) -> Any:
    """Run production HTTP clients against one synthetic source and event store."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(service.provider)) as provider_http, httpx.AsyncClient(transport=httpx.MockTransport(service.store)) as store_http:
        linkedin = LinkedInMDPClient("synthetic-token", http=provider_http, max_retries=0)
        supabase = SupabaseClient("https://store.example.test", "synthetic-key", http=store_http)
        return await sync_actual_actions(linkedin, supabase, account_profile_url=account, account_member_urn=urn, apply=apply, sources=sources)


async def source_envelopes(service: Services) -> ActualActionSources:
    """Acquire real client shapes for later boundary mutation scenarios."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(service.provider)) as http:
        return await acquire_actual_action_sources(LinkedInMDPClient("synthetic-token", http=http, max_retries=0))


async def matrix() -> dict[str, str]:
    """Assert all-stage refusal, positive event semantics, and duplicate-safe replay."""
    verdicts: dict[str, str] = {}

    def check(name: str, condition: bool) -> None:
        """Record a fixed safe verdict without person-level output."""
        assert condition, name
        verdicts[name] = "passed"

    service = Services()
    dry = await run_service(service)
    check("dry_run_zero_posts", dry.status == "dry_run" and dry.planned_total == 4 and service.store_gets == 1 and service.store_posts == 0)
    check("strict_source_gets_before_store", [request.url.params["domain"] for request in service.provider_calls if request.url.path.endswith("memberSnapshotData")] == ["INVITATIONS", "CONNECTIONS", "INBOX"] and service.provider_calls[-1].url.path.endswith("memberChangeLogs"))
    requested = int(service.provider_calls[-1].url.params["startTime"])
    expected = int((datetime.now(UTC) - timedelta(days=28)).timestamp() * 1000)
    check("bounded_changelog_request", abs(requested - expected) < 60_000 and service.provider_calls[-1].method == "GET")
    first = await run_service(service, apply=True)
    check("one_combined_duplicate_safe_post", first.status == "applied" and first.inserted == 4 and service.store_posts == 1 and len(service.posted[0]) == 4)
    types = {item["event_type"] for item in service.posted[0]}
    check("only_actual_event_types", types == {"LINKEDIN_INVITATION_HISTORY_FOUND", "LINKEDIN_CONNECTION_FOUND", "LINKEDIN_MESSAGE_OUTBOUND", "LINKEDIN_MESSAGE_INBOUND"})
    dms = [item for item in service.posted[0] if item["event_type"].startswith("LINKEDIN_MESSAGE_")]
    check("dm_evidence_payload", len(dms) == 2 and all(item["payload"]["message_kind"] == "dm" and item["payload"]["timestamp_semantics"] == "actual" and item["payload"]["read_state"] == "unknown" and item["payload"]["inbox_source_ref"].startswith("inbox.rows[") and item["payload"]["changelog_source_ref"].startswith("changelog.events[") and len(item["payload"]["source_artifact_digest"]) == 64 for item in dms))
    check("durable_matched_positive_sources", all(item["payload"]["source_evidence"]["inbox"] == service.inbox[int(item["payload"]["inbox_source_ref"].removeprefix("inbox.rows[").removesuffix("]"))] and item["payload"]["source_evidence"]["changelog"] == service.changes[int(item["payload"]["changelog_source_ref"].removeprefix("changelog.events[").removesuffix("]"))] and set(item["payload"]["source_evidence"]) == {"inbox", "changelog"} and "draft" not in item["payload"] for item in dms))
    expected_dm_key = "verified-dm-v1:" + hashlib.sha256(json.dumps([OWNER_URN, "call-activity-one"], separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()
    check("dm_key_uses_account_and_activity_only", expected_dm_key in {item["external_key"] for item in dms})
    media = message(minutes=30)
    media["CONTENT"] = ""
    media["ATTACHMENTS"] = [{"id": "synthetic-media-id"}]
    media_event = activity(media)
    media_event["activity"]["content"]["format"] = "MEDIA"
    media_event["activity"]["attachments"] = [{"id": "synthetic-media-id"}]
    media_service = Services(inbox=[media], changes=[media_event])
    media_summary = await run_service(media_service, apply=True)
    media_payload = next(item["payload"] for item in media_service.posted[0] if item["event_type"] == "LINKEDIN_MESSAGE_OUTBOUND")
    check("attachment_only_durable_evidence", media_summary.planned_by_type.get("LINKEDIN_MESSAGE_OUTBOUND") == 1 and media_payload["source_evidence"]["inbox"]["ATTACHMENTS"] == [{"id": "synthetic-media-id"}] and media_payload["source_evidence"]["changelog"]["activity"]["attachments"] == [{"id": "synthetic-media-id"}])
    invite = next(item for item in service.posted[0] if item["event_type"] == "LINKEDIN_INVITATION_HISTORY_FOUND")
    connection = next(item for item in service.posted[0] if item["event_type"] == "LINKEDIN_CONNECTION_FOUND")
    check("local_invite_time_and_connection_day", invite["payload"]["timestamp_semantics"] == "observed_at" and invite["payload"]["provider_timestamp_semantics"] == "local_time_timezone_unspecified" and connection["payload"]["timestamp_semantics"] == "event_date_day_precision")
    check("dm_create_instant_not_observation_time", all(item["occurred_at"] == datetime.fromtimestamp(next(change["activity"]["createdAt"] for change in service.changes if change["activityId"] == item["payload"]["activity_id"]) / 1000, UTC).isoformat() for item in dms))
    only_connection = Services(invitations=[], inbox=[], changes=[])
    connection_only_summary = await run_service(only_connection, apply=True)
    check("connection_not_invented_acceptance", connection_only_summary.planned_by_type == {"LINKEDIN_CONNECTION_FOUND": 1} and all(event["event_type"] != "LINKEDIN_INVITATION_ACCEPTED" for event in only_connection.posted[0]))
    future_connection = Services(connections=[{"URL": PEER, "Connected On": "2099-01-01"}], invitations=[], inbox=[], changes=[])
    try:
        await run_service(future_connection, apply=True)
    except ActualSyncError as exc:
        check("future_connection_day_blocks_store", exc.code == "connection_date_future" and future_connection.store_gets == 0 and future_connection.store_posts == 0)
    else:
        raise AssertionError("future connection day must block before store access")
    no_match = Services(prospects=[])
    unmatched_summary = await run_service(no_match, apply=True)
    check("unmatched_identity_never_created", unmatched_summary.planned_total == 0 and unmatched_summary.unmatched_dm == 2 and no_match.store_posts == 0)
    replay = await run_service(service, apply=True)
    check("replay_zero_new_events", replay.inserted == 0 and replay.already_present == 4 and len(service.state) == 4)
    reordered = Services(inbox=list(reversed(service.inbox)), changes=list(reversed(service.changes)), state=service.state)
    reordered_result = await run_service(reordered, apply=True)
    check("reordered_stable_dm_keys", reordered_result.inserted == 0 and {item["external_key"] for item in dms} == {item["external_key"] for item in reordered.posted[0] if item["event_type"].startswith("LINKEDIN_MESSAGE_")})
    escaped_key_service = Services()
    escaped_key_service.changes.append({"owner": OWNER_URN, "resourceName": "other", "method": "CREATE", "activityStatus": "SUCCESS", "activityId": "unrelated", "provider" + chr(0xD800): "unrelated metadata"})
    escaped_key_result = await run_service(escaped_key_service, apply=True)
    check("escaped_surrogate_metadata_does_not_block_verified_actions", escaped_key_result.inserted == 4 and escaped_key_service.store_posts == 1)

    escaped_snapshot = Services()
    escaped_snapshot.invitations.append({"Direction": "INCOMING", "inviteeProfileUrl": ACCOUNT, "provider" + chr(0xD800): "unrelated metadata"})
    snapshot_summary = await run_service(escaped_snapshot, apply=True)
    check("escaped_unmatched_snapshot_metadata_safe", snapshot_summary.inserted == 4 and escaped_snapshot.store_posts == 1)

    for failure in ("invitations_404", "malformed_inbox_envelope", "malformed_connection_paging", "inbox_truncated", "changelog_404", "changelog_truncated"):
        bad = Services(failure=failure)
        try:
            await run_service(bad, apply=True)
        except ActualSyncError:
            pass
        else:
            raise AssertionError(failure)
        check("source_blocks_" + failure, bad.store_gets == 0 and bad.store_posts == 0)
    for account, urn, name in (("https://attacker.invalid/in/x", OWNER_URN, "bad_account"), (ACCOUNT, "wrong-urn", "bad_urn")):
        bad = Services()
        try:
            await run_service(bad, apply=True, account=account, urn=urn)
        except ActualSyncError:
            pass
        else:
            raise AssertionError(name)
        check(name + "_before_provider", not bad.provider_calls and bad.store_gets == 0)
    invalid_account = Services()
    try:
        await run_service(invalid_account, apply=True, account=None)  # type: ignore[arg-type]
    except ActualSyncError:
        pass
    else:
        raise AssertionError("missing account")
    check("missing_account_before_provider", not invalid_account.provider_calls)
    invalid_apply = Services()
    try:
        await run_service(invalid_apply, apply="yes")  # type: ignore[arg-type]
    except ActualSyncError:
        pass
    else:
        raise AssertionError("nonboolean apply")
    check("nonboolean_apply_before_provider", not invalid_apply.provider_calls)
    good_sources = await source_envelopes(Services())
    for name, changed in (
        ("raw_export_mismatch", ActualActionSources({**good_sources.invitations, "raw_elements": [{"snapshotDomain": "INVITATIONS", "snapshotData": []}]}, good_sources.connections, good_sources.inbox, good_sources.changelog)),
        ("stale_acquisition", ActualActionSources({**good_sources.invitations, "completed_at": (datetime.now(UTC) - timedelta(days=2)).isoformat()}, good_sources.connections, good_sources.inbox, good_sources.changelog)),
        ("future_acquisition", ActualActionSources(good_sources.invitations, {**good_sources.connections, "completed_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()}, good_sources.inbox, good_sources.changelog)),
        ("missing_acquisition", ActualActionSources(good_sources.invitations, good_sources.connections, {**good_sources.inbox, "completed_at": None}, good_sources.changelog)),
        ("invalid_changelog_watermark", ActualActionSources(good_sources.invitations, good_sources.connections, good_sources.inbox, {**good_sources.changelog, "next_start_time": None})),
        ("invalid_changelog_shape", ActualActionSources(good_sources.invitations, good_sources.connections, good_sources.inbox, None)),  # type: ignore[arg-type]
        ("missing_changelog_scope", ActualActionSources(good_sources.invitations, good_sources.connections, good_sources.inbox, {key: value for key, value in good_sources.changelog.items() if key != "requested_start_time"})),
        ("wrong_changelog_scope", ActualActionSources(good_sources.invitations, good_sources.connections, good_sources.inbox, {**good_sources.changelog, "requested_start_time": 1})),
    ):
        bad = Services()
        try:
            await run_service(bad, apply=True, sources=changed)
        except ActualSyncError:
            pass
        else:
            raise AssertionError(name)
        check(name + "_zero_store_access", bad.store_gets == 0 and bad.store_posts == 0)
    malformed_row = message()
    malformed_row.pop("CONVERSATION ID")
    malformed = Services(inbox=[malformed_row], changes=[])
    try:
        await run_service(malformed, apply=True)
    except ActualSyncError:
        pass
    else:
        raise AssertionError("unattributed inbox row")
    check("unattributed_inbox_blocks_all", malformed.store_gets == 0 and malformed.store_posts == 0)
    try:
        plan_verified_dm_events({"reply": []}, {PEER: "prospect-one"}, account_member_urn=OWNER_URN, source_digest="0" * 64, acquired_at=datetime.now(UTC), inbox=good_sources.inbox, changelog=good_sources.changelog)  # type: ignore[arg-type]
    except ActualSyncError:
        verdicts["recommendation_input_refused"] = "passed"
    else:
        raise AssertionError("report recommendation accepted as actual evidence")

    note_row = message()
    note_event = activity(note_row)
    note_event["activity"]["extensionContent"] = {"contentRecordMap": {"InvitationMessageContent": {"text": note_row["CONTENT"]}}}
    note_service = Services(inbox=[note_row], changes=[note_event])
    note_result = await run_service(note_service, apply=True)
    check("invitation_note_never_actual_dm", note_result.planned_by_type.get("LINKEDIN_MESSAGE_OUTBOUND", 0) == 0 and len(note_service.state) == 2)
    observed = Services(inbox=[message()], changes=[])
    observed_result = await run_service(observed, apply=True)
    check("observed_only_never_actual_dm", observed_result.planned_by_type.get("LINKEDIN_MESSAGE_OUTBOUND", 0) == 0 and len(observed.state) == 2)
    group = message(thread="group-thread")
    group["RECIPIENT PROFILE URLS"] = [PEER, OTHER]
    grouped = Services(inbox=[group], changes=[activity(group)])
    grouped_result = await run_service(grouped, apply=True)
    check("group_never_actual_dm", grouped_result.planned_by_type.get("LINKEDIN_MESSAGE_OUTBOUND", 0) == 0)
    isolated = Services(inbox=[message(), group], changes=[activity(message())])
    isolated_result = await run_service(isolated, apply=True)
    check("scoped_unknown_keeps_positive", isolated_result.planned_by_type.get("LINKEDIN_MESSAGE_OUTBOUND", 0) == 1 and isolated_result.uncertain_count > 0)
    unknown = activity(message())
    unknown["activity"]["extensionContent"] = {"contentRecordMap": {"Unsupported": {}}}
    unsupported = Services(inbox=[message()], changes=[unknown])
    unsupported_result = await run_service(unsupported, apply=True)
    check("unknown_extension_never_actual_dm", unsupported_result.planned_by_type.get("LINKEDIN_MESSAGE_OUTBOUND", 0) == 0)
    conflicted = activity(message())
    conflicted["activityStatus"] = "SUCCESSFUL_REPLAY"
    conflicted["activity"]["content"]["fallback"] = "Different synthetic content"
    replay_conflict = Services(inbox=[message()], changes=[activity(message()), conflicted])
    replay_conflict_result = await run_service(replay_conflict, apply=True)
    check("conflicting_replay_never_actual_dm", replay_conflict_result.planned_by_type.get("LINKEDIN_MESSAGE_OUTBOUND", 0) == 0 and replay_conflict_result.uncertain_count > 0)
    conflict = Services(failure="prospect_conflict")
    try:
        await run_service(conflict, apply=True)
    except ActualSyncError:
        pass
    else:
        raise AssertionError("prospect conflict")
    check("conflicting_prospect_index_zero_posts", conflict.store_posts == 0)
    timeout = Services(failure="timeout_after_commit")
    try:
        await run_service(timeout, apply=True)
    except ActualSyncError as exc:
        check("timeout_outcome_unknown", exc.code == "persistence_outcome_unknown" and len(timeout.state) == 4)
    else:
        raise AssertionError("timeout must be uncertain")
    replay_after_timeout = Services(state=timeout.state)
    replay_summary = await run_service(replay_after_timeout, apply=True)
    check("timeout_safe_replay", replay_summary.inserted == 0 and replay_summary.already_present == 4)

    class InvalidCountStore:
        """Return a forbidden count after a valid prospect index lookup."""

        def __init__(self, value: Any) -> None:
            """Retain one malformed insert response value."""
            self.value = value

        async def prospect_ids_by_linkedin_key(self) -> dict[str, str]:
            """Return one exact synthetic identity."""
            return {PEER: "prospect-one"}

        async def insert_events_ignore_duplicates(self, events: list[dict[str, Any]]) -> Any:
            """Model an invalid count after a possibly committed batch."""
            return self.value

    for name, value in (("boolean", True), ("negative", -1), ("above_total", 5)):
        try:
            await sync_actual_actions(None, InvalidCountStore(value), account_profile_url=ACCOUNT, account_member_urn=OWNER_URN, apply=True, sources=good_sources)
        except ActualSyncError as exc:
            check(name + "_insert_count_unknown", exc.code == "persistence_outcome_unknown")
        else:
            raise AssertionError(name + " inserted count")

    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "private-evidence"
        service = Services()
        async with httpx.AsyncClient(transport=httpx.MockTransport(service.provider)) as provider_http, httpx.AsyncClient(transport=httpx.MockTransport(service.store)) as store_http:
            linkedin = LinkedInMDPClient("synthetic-token", http=provider_http, max_retries=0)
            supabase = SupabaseClient("https://store.example.test", "synthetic-key", http=store_http)
            stdout = io.StringIO()
            with patch.object(cli.LinkedInMDPClient, "from_env", return_value=linkedin), patch.object(cli.SupabaseClient, "from_env", return_value=supabase), contextlib.redirect_stdout(stdout):
                code = await cli.run(False, ACCOUNT, OWNER_URN, output)
        evidence = json.loads((output / "actual-action-sync.json").read_text(encoding="utf-8"))
        check("cli_dry_default_private_manifest", code == 0 and stdout.getvalue() == "DRY_RUN_COMPLETE\n" and service.store_posts == 0 and evidence["status"] == "dry_run" and stat.S_IMODE(output.stat().st_mode) == 0o700 and stat.S_IMODE((output / "actual-action-sync.json").stat().st_mode) == 0o600)
        no_output = io.StringIO()
        with patch.object(cli.LinkedInMDPClient, "from_env", side_effect=AssertionError("must not construct client")), contextlib.redirect_stdout(no_output):
            code = await cli.run(True, ACCOUNT, OWNER_URN, output)
        check("cli_existing_output_refused_before_clients", code == 1 and no_output.getvalue() == "FAILED\n")
    service = Services()
    async with httpx.AsyncClient(transport=httpx.MockTransport(service.provider)) as provider_http, httpx.AsyncClient(transport=httpx.MockTransport(service.store)) as store_http:
        linkedin = LinkedInMDPClient("synthetic-token", http=provider_http, max_retries=0)
        supabase = SupabaseClient("https://store.example.test", "synthetic-key", http=store_http)
        stdout = io.StringIO()
        with patch.object(cli.LinkedInMDPClient, "from_env", return_value=linkedin), patch.object(cli.SupabaseClient, "from_env", return_value=supabase), contextlib.redirect_stdout(stdout):
            code = await cli.run(True, ACCOUNT, OWNER_URN)
    check("cli_apply_is_explicit", code == 0 and stdout.getvalue() == "APPLIED\n" and service.store_posts == 1)
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "partial"
        service = Services()
        async with httpx.AsyncClient(transport=httpx.MockTransport(service.provider)) as provider_http, httpx.AsyncClient(transport=httpx.MockTransport(service.store)) as store_http:
            linkedin = LinkedInMDPClient("synthetic-token", http=provider_http, max_retries=0)
            supabase = SupabaseClient("https://store.example.test", "synthetic-key", http=store_http)
            stdout = io.StringIO()
            with patch.object(cli.LinkedInMDPClient, "from_env", return_value=linkedin), patch.object(cli.SupabaseClient, "from_env", return_value=supabase), patch.object(cli.os, "fdopen", side_effect=OSError("synthetic manifest failure")), contextlib.redirect_stdout(stdout):
                code = await cli.run(True, ACCOUNT, OWNER_URN, output)
        check("cli_committed_manifest_failure_explicit", code == 1 and stdout.getvalue() == "APPLIED_DIAGNOSTIC_FAILED\n" and service.store_posts == 1 and not output.exists())
    service = Services()
    async with httpx.AsyncClient(transport=httpx.MockTransport(service.provider)) as provider_http, httpx.AsyncClient(transport=httpx.MockTransport(service.store)) as store_http:
        linkedin = LinkedInMDPClient("synthetic-token", http=provider_http, max_retries=0)
        supabase = SupabaseClient("https://store.example.test", "synthetic-key", http=store_http)
        stdout = io.StringIO()
        with patch.object(cli.LinkedInMDPClient, "from_env", return_value=linkedin), patch.object(cli.SupabaseClient, "from_env", return_value=supabase), patch.object(linkedin, "aclose", side_effect=OSError("synthetic close failure")), contextlib.redirect_stdout(stdout):
            code = await cli.run(True, ACCOUNT, OWNER_URN)
    check("cli_committed_close_failure_explicit", code == 1 and stdout.getvalue() == "APPLIED_DIAGNOSTIC_FAILED\n" and service.store_posts == 1)
    service = Services(failure="timeout_after_commit")
    async with httpx.AsyncClient(transport=httpx.MockTransport(service.provider)) as provider_http, httpx.AsyncClient(transport=httpx.MockTransport(service.store)) as store_http:
        linkedin = LinkedInMDPClient("synthetic-token", http=provider_http, max_retries=0)
        supabase = SupabaseClient("https://store.example.test", "synthetic-key", http=store_http)
        stdout = io.StringIO()
        with patch.object(cli.LinkedInMDPClient, "from_env", return_value=linkedin), patch.object(cli.SupabaseClient, "from_env", return_value=supabase), contextlib.redirect_stdout(stdout):
            code = await cli.run(True, ACCOUNT, OWNER_URN)
    check("cli_unknown_write_fixed_diagnostic", code == 1 and stdout.getvalue() == "PERSISTENCE_OUTCOME_UNKNOWN\n" and len(service.state) == 4)
    return verdicts


def main(path: Path | None = None) -> None:
    """Reset a sanitized verdict artifact, then record the current synthetic run."""
    target = path or ROOT / "artifacts/actual-action-sync-e2e-evidence.json"
    target.write_text('{"run_status":"running","scenarios":{}}\n', encoding="utf-8")
    try:
        verdicts = asyncio.run(matrix())
    except BaseException:
        target.write_text('{"run_status":"failed","scenarios":{}}\n', encoding="utf-8")
        raise
    target.write_text(json.dumps({"run_status": "passed", "scenarios": verdicts}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"actual action sync E2E: {len(verdicts)} passed")


def failure_artifact_scenario() -> None:
    """Ensure a failed rerun cannot leave an older passing verdict artifact."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "evidence.json"
        path.write_text('{"run_status":"passed"}\n', encoding="utf-8")
        original = globals()["matrix"]

        async def fail() -> dict[str, str]:
            """Force a synthetic current-run failure after the artifact reset."""
            raise AssertionError("synthetic failure")

        globals()["matrix"] = fail
        try:
            try:
                main(path)
            except AssertionError:
                pass
            else:
                raise AssertionError("forced failure did not fail")
        finally:
            globals()["matrix"] = original
        assert json.loads(path.read_text(encoding="utf-8"))["run_status"] == "failed"


if __name__ == "__main__":
    main()
