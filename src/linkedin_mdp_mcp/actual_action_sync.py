"""Stage real LinkedIn evidence and optionally persist one duplicate-safe action batch."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from linkedin_mdp_mcp.connection_sync import plan_connection_events
from linkedin_mdp_mcp.dm_actions import DmEvidence, classify_dm_evidence
from linkedin_mdp_mcp.inbox_sync import normalize_profile_url
from linkedin_mdp_mcp.invitation_shortlist import (
    ShortlistInputError,
    _validate_provider_snapshot,
)
from linkedin_mdp_mcp.invitation_sync import plan_invitation_events

SOURCE_MAX_AGE = timedelta(days=1)
CHANGELOG_WINDOW = timedelta(days=28)
CLASSIFIER_VERSION = "dm-positive-v1"


class ActualSyncError(RuntimeError):
    """Carry a fixed diagnostic code without exposing source or service data."""

    def __init__(self, code: str) -> None:
        """Store only a stable, private-data-free failure code."""
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ActualActionSources:
    """Hold every completed provider export before any prospect or event request."""

    invitations: Mapping[str, Any]
    connections: Mapping[str, Any]
    inbox: Mapping[str, Any]
    changelog: Mapping[str, Any]


@dataclass(frozen=True)
class DmSyncPlan:
    """Hold only verified positive DM events and unmatched identity counts."""

    events: tuple[dict[str, Any], ...]
    unmatched: int


@dataclass(frozen=True)
class ActualActionSyncSummary:
    """Describe planned and applied actual actions without implying an unmade write."""

    status: Literal["dry_run", "applied"]
    planned_by_type: dict[str, int]
    planned_total: int
    inserted: int | None
    already_present: int | None
    uncertain_count: int
    unmatched_dm: int


def _aware(value: Any) -> datetime | None:
    """Parse only an explicitly timezone-aware ISO acquisition instant."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(UTC) if parsed.tzinfo and parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _fresh(value: Any, now: datetime) -> datetime:
    """Require a real recent acquisition instant rather than provider freshness guesses."""
    acquired = _aware(value)
    if acquired is None or acquired > now or now - acquired > SOURCE_MAX_AGE:
        raise ActualSyncError("source_acquisition_invalid")
    return acquired


def _digest(value: Any) -> str:
    """Bind event pointers to a canonical private source artifact without logging it."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ActualSyncError("source_digest_invalid") from exc
    return hashlib.sha256(encoded).hexdigest()


def _matched_source(source: Mapping[str, Any], field: str, ref: str, prefix: str) -> dict[str, Any]:
    """Resolve one classifier pointer to its exact retained positive source row."""
    match = re.fullmatch(rf"{re.escape(prefix)}\[(0|[1-9][0-9]*)\]", ref)
    rows = source.get(field)
    if match is None or not isinstance(rows, list) or int(match.group(1)) >= len(rows):
        raise ActualSyncError("dm_source_pointer_invalid")
    row = rows[int(match.group(1))]
    if not isinstance(row, Mapping):
        raise ActualSyncError("dm_source_pointer_invalid")
    return dict(row)


async def acquire_actual_action_sources(linkedin: Any) -> ActualActionSources:
    """Read all three strict snapshots and a bounded changelog before store access."""
    snapshots: dict[str, Mapping[str, Any]] = {}
    for domain in ("INVITATIONS", "CONNECTIONS", "INBOX"):
        result = await linkedin.snapshot(domain, max_pages=50, strict_elements=True)
        if not isinstance(result, Mapping):
            raise ActualSyncError("source_snapshot_invalid")
        snapshots[domain] = {**result, "source_result": "success", "completed_at": datetime.now(UTC).isoformat()}
    requested_at = datetime.now(UTC)
    start = int((requested_at - CHANGELOG_WINDOW).timestamp() * 1000)
    log = await linkedin.changelog(start_time=start, count=50, max_pages=50)
    if not isinstance(log, Mapping):
        raise ActualSyncError("changelog_invalid")
    changelog = {**log, "collected_at": datetime.now(UTC).isoformat(), "requested_at": requested_at.isoformat(), "requested_start_time": start}
    return ActualActionSources(snapshots["INVITATIONS"], snapshots["CONNECTIONS"], snapshots["INBOX"], changelog)


def _validate_sources(sources: ActualActionSources, *, account_profile_url: str, account_member_urn: str, now: datetime) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], DmEvidence, str, datetime, datetime]:
    """Reject global transport and coverage gaps before looking up prospects."""
    if not isinstance(sources, ActualActionSources) or any(not isinstance(part, Mapping) for part in (sources.invitations, sources.connections, sources.inbox, sources.changelog)):
        raise ActualSyncError("sources_invalid")
    try:
        invitations = _validate_provider_snapshot(sources.invitations, "INVITATIONS")
        connections = _validate_provider_snapshot(sources.connections, "CONNECTIONS")
        _validate_provider_snapshot(sources.inbox, "INBOX")
    except (ShortlistInputError, TypeError, ValueError) as exc:
        raise ActualSyncError("source_snapshot_invalid") from exc
    invitation_time = _fresh(sources.invitations.get("completed_at"), now)
    connection_time = _fresh(sources.connections.get("completed_at"), now)
    _fresh(sources.inbox.get("completed_at"), now)
    changelog_time = _fresh(sources.changelog.get("collected_at"), now)
    requested_at = _fresh(sources.changelog.get("requested_at"), now)
    requested_start = sources.changelog.get("requested_start_time")
    if requested_at > changelog_time or type(requested_start) is not int or requested_start != int((requested_at - CHANGELOG_WINDOW).timestamp() * 1000):
        raise ActualSyncError("changelog_scope_invalid")
    try:
        evidence = classify_dm_evidence(sources.inbox, sources.changelog, account_profile_url=account_profile_url, account_member_urn=account_member_urn, now=now)
    except (ValueError, TypeError, KeyError) as exc:
        raise ActualSyncError("dm_evidence_invalid") from exc
    if evidence.coverage_reasons:
        raise ActualSyncError("source_coverage_incomplete")
    digest = _digest({"inbox": sources.inbox, "changelog": sources.changelog})
    return invitations, connections, evidence, digest, invitation_time, connection_time


def plan_verified_dm_events(evidence: DmEvidence, prospects_by_key: Mapping[str, str], *, account_member_urn: str, source_digest: str, acquired_at: datetime, inbox: Mapping[str, Any], changelog: Mapping[str, Any]) -> DmSyncPlan:
    """Convert only classifier-proven positive DMs to stable directional events."""
    if not isinstance(evidence, DmEvidence) or evidence.coverage_reasons:
        raise ActualSyncError("dm_evidence_invalid")
    if not isinstance(account_member_urn, str) or not account_member_urn.startswith("urn:li:person:") or not account_member_urn.removeprefix("urn:li:person:"):
        raise ActualSyncError("account_invalid")
    if not isinstance(source_digest, str) or len(source_digest) != 64 or acquired_at.tzinfo is None or acquired_at.utcoffset() is None:
        raise ActualSyncError("source_digest_invalid")
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    unmatched = 0
    for message in evidence.verified:
        prospect_id = prospects_by_key.get(message.profile_url)
        if prospect_id is None:
            unmatched += 1
            continue
        if message.direction not in {"outbound", "inbound"} or not message.activity_id or message.read_state != "unknown":
            raise ActualSyncError("dm_evidence_invalid")
        identity = _digest([account_member_urn, message.activity_id])
        key = f"verified-dm-v1:{identity}"
        if key in seen:
            raise ActualSyncError("dm_identity_duplicate")
        seen.add(key)
        source_inbox = _matched_source(inbox, "rows", message.inbox_ref, "inbox.rows")
        source_event = _matched_source(changelog, "events", message.changelog_ref, "changelog.events")
        if source_event.get("activityId") != message.activity_id or source_event.get("resourceName") != "messages":
            raise ActualSyncError("dm_source_pointer_invalid")
        events.append({
            "prospect_id": prospect_id,
            "source": "LINKEDIN_MDP",
            "event_type": "LINKEDIN_MESSAGE_OUTBOUND" if message.direction == "outbound" else "LINKEDIN_MESSAGE_INBOUND",
            "external_key": key,
            "occurred_at": message.occurred_at.astimezone(UTC).isoformat(),
            "payload": {
                "timestamp_semantics": "actual", "message_kind": "dm", "read_state": "unknown",
                "canonical_profile_url": message.profile_url, "thread": message.thread,
                "activity_id": message.activity_id, "inbox_source_ref": message.inbox_ref,
                "changelog_source_ref": message.changelog_ref, "source_artifact_digest": source_digest,
                "source_acquired_at": acquired_at.astimezone(UTC).isoformat(),
                "classifier_version": CLASSIFIER_VERSION,
                "source_evidence": {"inbox": source_inbox, "changelog": source_event},
            },
        })
    return DmSyncPlan(tuple(events), unmatched)


async def sync_actual_actions(linkedin: Any, supabase: Any, *, account_profile_url: str, account_member_urn: str, apply: bool = False, sources: ActualActionSources | None = None) -> ActualActionSyncSummary:
    """Stage and validate sources, plan known identities, and optionally insert once."""
    if type(apply) is not bool:
        raise ActualSyncError("apply_flag_invalid")
    if not isinstance(account_profile_url, str) or normalize_profile_url(account_profile_url) != account_profile_url or not isinstance(account_member_urn, str) or not account_member_urn.startswith("urn:li:person:") or not account_member_urn.removeprefix("urn:li:person:"):
        raise ActualSyncError("account_invalid")
    if sources is None:
        try:
            sources = await acquire_actual_action_sources(linkedin)
        except ActualSyncError:
            raise
        except Exception as exc:
            raise ActualSyncError("source_acquisition_failed") from exc
    now = datetime.now(UTC)
    invitations, connections, evidence, digest, invitation_time, connection_time = _validate_sources(sources, account_profile_url=account_profile_url, account_member_urn=account_member_urn, now=now)
    for row in connections:
        connected_on = row.get("Connected On")
        if not isinstance(connected_on, str):
            continue
        try:
            connected_date = date.fromisoformat(connected_on.strip())
        except ValueError:
            continue
        if connected_date > connection_time.date():
            raise ActualSyncError("connection_date_future")
    try:
        prospects = await supabase.prospect_ids_by_linkedin_key()
    except Exception as exc:
        raise ActualSyncError("prospect_index_unavailable") from exc
    if not isinstance(prospects, Mapping) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in prospects.items()):
        raise ActualSyncError("prospect_index_invalid")
    invitation_plan = plan_invitation_events(invitations, prospects, observed_at=invitation_time)
    connection_plan = plan_connection_events(connections, prospects, observed_at=connection_time)
    dm_plan = plan_verified_dm_events(evidence, prospects, account_member_urn=account_member_urn, source_digest=digest, acquired_at=_fresh(sources.changelog.get("collected_at"), now), inbox=sources.inbox, changelog=sources.changelog)
    events = [*invitation_plan.events, *connection_plan.events, *dm_plan.events]
    identities = [(item["source"], item["external_key"]) for item in events]
    if len(identities) != len(set(identities)):
        raise ActualSyncError("event_identity_duplicate")
    planned_by_type = dict(sorted(Counter(item["event_type"] for item in events).items()))
    if not apply:
        return ActualActionSyncSummary("dry_run", planned_by_type, len(events), None, None, len(evidence.uncertain), dm_plan.unmatched)
    try:
        inserted = await supabase.insert_events_ignore_duplicates(events) if events else 0
    except Exception as exc:
        raise ActualSyncError("persistence_outcome_unknown") from exc
    if type(inserted) is not int or not 0 <= inserted <= len(events):
        raise ActualSyncError("persistence_outcome_unknown")
    return ActualActionSyncSummary("applied", planned_by_type, len(events), inserted, len(events) - inserted, len(evidence.uncertain), dm_plan.unmatched)
