"""Classify positive LinkedIn DM evidence and plan private manual actions."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from linkedin_mdp_mcp.inbox_sync import (
    _attachments,
    _participants,
    _safe_one_to_one_row,
    _timestamp,
    normalize_profile_url,
)
from linkedin_mdp_mcp.invitation_shortlist import (
    ShortlistInputError,
    _suppressed,
    _validate_database_snapshot,
    _validate_provider_snapshot,
)


@dataclass(frozen=True)
class DmMessage:
    """Hold one uniquely correlated real DM and both source pointers."""

    profile_url: str
    thread: str
    direction: str
    occurred_at: datetime
    content: str
    inbox_ref: str
    changelog_ref: str
    activity_id: str
    read_state: str = "unknown"


@dataclass(frozen=True)
class DmUnknown:
    """Hold an observation that may change a prospect's action decision."""

    profile_url: str | None
    thread: str | None
    direction: str | None
    occurred_at: datetime | None
    reason: str
    source_ref: str


@dataclass(frozen=True)
class DmEvidence:
    """Hold complete immutable positive evidence, uncertainty, and coverage."""

    verified: tuple[DmMessage, ...]
    uncertain: tuple[DmUnknown, ...]
    tainted_threads: tuple[str, ...]
    coverage_reasons: tuple[str, ...]
    acquired_at: datetime | None
    upstream_freshness: str
    changelog_scope: str = "consent_limited_28_days"


def _aware_time(value: Any) -> datetime | None:
    """Accept only an explicitly timezone-aware ISO instant."""
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(UTC) if result.tzinfo and result.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _event_time(value: Any) -> datetime | None:
    """Decode a positive integer epoch in milliseconds without boolean coercion."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _row_time(row: Mapping[str, Any], observed_at: datetime) -> datetime | None:
    """Use only provider INBOX time with explicit UTC semantics."""
    parsed = _timestamp(row.get("DATE"), observed_at)
    if parsed is None or parsed[2] != "actual":
        return None
    return _aware_time(parsed[0])


def _row_identity(row: Mapping[str, Any], account: str) -> tuple[str | None, str | None]:
    """Identify a two-person message peer and its direction."""
    if not _safe_one_to_one_row(row, account):
        return None, None
    sender = normalize_profile_url(row.get("SENDER PROFILE URL"))
    recipients = row.get("RECIPIENT PROFILE URLS")
    if isinstance(recipients, str):
        recipients = [recipients]
    recipient = normalize_profile_url(recipients[0]) if isinstance(recipients, list) and len(recipients) == 1 else None
    if sender == account:
        return recipient, "outbound"
    return sender, "inbound"


def _thread_suffix(value: Any, *, activity: bool = False) -> str | None:
    """Extract the exact opaque thread suffix from supported IDs."""
    if not isinstance(value, str) or not value.strip():
        return None
    prefix = "urn:li:messagingThread:"
    if activity and not value.startswith(prefix):
        return None
    if ":" in value and not value.startswith(prefix):
        return None
    suffix = value.removeprefix(prefix)
    return suffix if suffix and suffix == suffix.strip() and ":" not in suffix else None


def _content(activity: Mapping[str, Any]) -> tuple[str | None, str]:
    """Separate typed invitation notes, supported DMs, and unknown extensions."""
    extension = activity.get("extensionContent")
    if extension is not None:
        if not isinstance(extension, Mapping) or not isinstance(extension.get("contentRecordMap"), Mapping):
            return None, "unknown_extension"
        records = extension["contentRecordMap"]
        if "InvitationMessageContent" in records:
            return None, "invitation_note"
        if records:
            return None, "unknown_extension"
    item = activity.get("content")
    if not isinstance(item, Mapping) or item.get("format") not in ("TEXT", "MEDIA") or item.get("formatVersion") != 1:
        return None, "unsupported_content"
    fallback = item.get("fallback")
    if not isinstance(fallback, str) or (item["format"] == "TEXT" and not fallback.strip()):
        return None, "unsupported_content"
    nested = item.get("content")
    if nested is not None and (not isinstance(nested, Mapping) or not isinstance(nested.get("string"), str) or nested["string"] != fallback):
        return None, "content_conflict"
    attachments = activity.get("attachments")
    if not isinstance(attachments, list) or any(not isinstance(attachment, Mapping) for attachment in attachments):
        return None, "unsupported_attachments"
    if item["format"] == "TEXT" and attachments:
        return None, "unsupported_attachments"
    if item["format"] == "MEDIA" and not attachments:
        return None, "unsupported_attachments"
    return fallback, "dm"


def _fingerprint(value: Any) -> str | None:
    """Canonicalize a JSON activity, rejecting malformed replay shapes."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        return None


def _citation_url(value: Any) -> bool:
    """Require a usable HTTPS citation without credentials or malformed hosts."""
    if not isinstance(value, str) or any(char.isspace() or ord(char) < 32 or char == "\\" for char in value):
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.netloc or not host or parsed.username is not None or parsed.password is not None:
        return False
    if port is not None and port == 0:
        return False
    labels = host.removesuffix(".").split(".")
    return all(label and len(label) <= 63 and label.isascii() and label[0].isalnum() and label[-1].isalnum() and all(char.isalnum() or char == "-" for char in label) for label in labels)


def classify_dm_evidence(
    inbox: Mapping[str, Any],
    changelog: Mapping[str, Any],
    *,
    account_profile_url: str,
    account_member_urn: str,
    now: datetime,
) -> DmEvidence:
    """Prove real DMs by unique full-shape CREATE-to-INBOX correlation."""
    account = normalize_profile_url(account_profile_url)
    if account is None or not isinstance(account_member_urn, str) or not account_member_urn.startswith("urn:li:person:") or not account_member_urn.removeprefix("urn:li:person:") or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("account_or_clock_invalid")
    now = now.astimezone(UTC)
    reasons: list[str] = []
    acquired = _aware_time(inbox.get("completed_at")) if isinstance(inbox, Mapping) else None
    if acquired is None or acquired > now or now - acquired > timedelta(days=1):
        reasons.append("inbox_stale_or_missing_acquisition")
    changelog_acquired = _aware_time(changelog.get("collected_at")) if isinstance(changelog, Mapping) else None
    if changelog_acquired is None or changelog_acquired > now or now - changelog_acquired > timedelta(days=1):
        reasons.append("changelog_stale_or_missing_acquisition")
    try:
        rows = _validate_provider_snapshot(inbox, "INBOX")
    except (ShortlistInputError, TypeError):
        rows = []
        reasons.append("inbox_incomplete")
    events_value = changelog.get("events") if isinstance(changelog, Mapping) else None
    watermark = changelog.get("next_start_time") if isinstance(changelog, Mapping) else None
    if (
        not isinstance(events_value, list)
        or any(not isinstance(item, Mapping) for item in events_value)
        or changelog.get("truncated") is not False
        or not isinstance(changelog.get("page_count"), int)
        or isinstance(changelog.get("page_count"), bool)
        or changelog["page_count"] < 1
        or not (watermark is None and events_value == [] or isinstance(watermark, int) and not isinstance(watermark, bool) and watermark > 0)
    ):
        events: list[Mapping[str, Any]] = []
        reasons.append("changelog_incomplete")
    else:
        events = events_value
    upstream_freshness = "unknown" if not isinstance(inbox, Mapping) or inbox.get("provider_generated_at") is None else "provided"
    if not rows and "inbox_incomplete" in reasons:
        return DmEvidence((), (), (), tuple(sorted(set(reasons))), acquired, upstream_freshness)

    tainted: set[str] = set()
    unidentified_threads: set[str] = set()
    peers_by_thread: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        thread, participants, malformed = _participants(row)
        if thread is None:
            reasons.append("unattributed_inbox_row")
            continue
        suffix = _thread_suffix(thread)
        if suffix is None:
            tainted.add(thread)
            continue
        peers_by_thread[suffix].update(participants - {account})
        if malformed or account not in participants:
            unidentified_threads.add(suffix)
        if malformed or not _safe_one_to_one_row(row, account) or len(participants) != 2:
            tainted.add(suffix)
        if len(peers_by_thread[suffix]) != 1:
            tainted.add(suffix)
    row_meta: list[tuple[str | None, str | None, str | None, datetime | None]] = []
    for row in rows:
        thread = _thread_suffix(row.get("CONVERSATION ID"))
        peer, direction = _row_identity(row, account)
        row_meta.append((thread, peer, direction, _row_time(row, acquired or now)))

    by_activity: dict[str, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for index, event in enumerate(events):
        if event.get("resourceName") != "messages":
            continue
        activity_id = event.get("activityId")
        key = activity_id if isinstance(activity_id, str) and activity_id else f"missing:{index}"
        by_activity[key].append((index, event))
    uncertain: list[DmUnknown] = []
    verified: list[DmMessage] = []
    matched_rows: set[int] = set()
    note_rows: set[int] = set()
    owners = {event.get("owner") for copies in by_activity.values() for _, event in copies if isinstance(event.get("owner"), str)}
    owner = account_member_urn if owners == {account_member_urn} or not by_activity else None
    if by_activity and (owner is None or not owner.startswith("urn:li:person:")):
        reasons.append("owner_identity_conflict")
    author_profiles: dict[str, set[str]] = defaultdict(set)
    profile_authors: dict[str, set[str]] = defaultdict(set)
    for copies in by_activity.values():
        for _, event in copies:
            activity = event.get("activity")
            if not isinstance(activity, Mapping):
                continue
            suffix = _thread_suffix(activity.get("thread"), activity=True)
            author = activity.get("author")
            if not suffix or not isinstance(author, str):
                continue
            for thread, peer, direction, _ in row_meta:
                if thread != suffix or peer is None or direction is None:
                    continue
                sender = account if direction == "outbound" else peer
                if (author == owner) == (sender == account):
                    author_profiles[author].add(sender)
                    profile_authors[sender].add(author)

    for copies in by_activity.values():
        index, event = copies[0]
        activity = event.get("activity")
        suffix = _thread_suffix(activity.get("thread"), activity=True) if isinstance(activity, Mapping) else None
        peer_set = peers_by_thread.get(suffix or "", set())
        peer = next(iter(peer_set)) if len(peer_set) == 1 else None
        author = activity.get("author") if isinstance(activity, Mapping) else None
        direction = "outbound" if author == owner and owner else "inbound" if isinstance(author, str) and owner else None
        moment = _event_time(activity.get("createdAt")) if isinstance(activity, Mapping) else None
        ref = f"changelog.events[{index}]"
        fingerprints = [_fingerprint({k: v for k, v in copy.items() if k not in {"activityStatus", "processedAt", "capturedAt", "id"}}) for _, copy in copies]
        if None in fingerprints or len(set(fingerprints)) > 1 or any(copy.get("activityStatus") not in {"SUCCESS", "SUCCESSFUL_REPLAY"} for _, copy in copies):
            uncertain.append(DmUnknown(peer, suffix, direction, moment, "conflicting_replay", ref))
            continue
        if not isinstance(activity, Mapping) or event.get("method") != "CREATE" or event.get("activityStatus") not in {"SUCCESS", "SUCCESSFUL_REPLAY"}:
            uncertain.append(DmUnknown(peer, suffix, direction, moment, "not_successful_create", ref))
            continue
        text, kind = _content(activity)
        identity_valid = (
            owner is not None and event.get("owner") == owner and event.get("actor") == author
            and activity.get("owner") == author and isinstance(author, str)
            and author.startswith("urn:li:person:") and isinstance(event.get("activityId"), str)
            and bool(event["activityId"]) and isinstance(event.get("resourceId"), str)
            and bool(event["resourceId"]) and event["resourceId"] == activity.get("id")
            and moment is not None and moment <= now and suffix is not None
            and suffix not in tainted and peer is not None and direction is not None
            and len(author_profiles[author]) == 1
            and len(profile_authors[account if direction == "outbound" else peer]) == 1
        )
        if not identity_valid:
            uncertain.append(DmUnknown(peer, suffix, direction, moment, "identity_or_shape_invalid", ref))
            continue
        if peer is None or suffix is None or direction is None or moment is None:
            uncertain.append(DmUnknown(peer, suffix, direction, moment, "identity_or_shape_invalid", ref))
            continue
        if kind == "invitation_note":
            content = activity.get("content")
            note_text = content.get("fallback") if isinstance(content, Mapping) else None
            candidates = [i for i, (thread, row_peer, row_direction, row_moment) in enumerate(row_meta) if thread == suffix and row_peer == peer and row_direction == direction and moment and row_moment and int(moment.timestamp()) == int(row_moment.timestamp()) and isinstance(rows[i].get("CONTENT"), str) and rows[i]["CONTENT"] == note_text]
            if len(candidates) == 1:
                note_rows.add(candidates[0])
            else:
                uncertain.append(DmUnknown(peer, suffix, direction, moment, "uncorrelated_invitation_note", ref))
            continue
        if kind != "dm" or text is None:
            uncertain.append(DmUnknown(peer, suffix, direction, moment, kind if kind != "dm" else "identity_or_shape_invalid", ref))
            continue
        candidates = [i for i, (thread, row_peer, row_direction, row_moment) in enumerate(row_meta) if thread == suffix and row_peer == peer and row_direction == direction and row_moment and int(moment.timestamp()) == int(row_moment.timestamp()) and rows[i].get("CONTENT", "") == text and _attachments(rows[i]) in ({}, {"ATTACHMENTS": activity.get("attachments")}) and (rows[i].get("ATTACHMENTS") or []) == activity.get("attachments")]
        if len(candidates) != 1 or candidates[0] in matched_rows:
            uncertain.append(DmUnknown(peer, suffix, direction, moment, "ambiguous_correlation", ref))
            continue
        row_index = candidates[0]
        matched_rows.add(row_index)
        verified.append(DmMessage(peer, suffix, direction, moment, text, f"inbox.rows[{row_index}]", ref, str(event["activityId"])))
    for index, (thread, peer, direction, moment) in enumerate(row_meta):
        if index not in matched_rows and index not in note_rows:
            uncertain.append(DmUnknown(peer, thread, direction, moment, "unverified_inbox_observation", f"inbox.rows[{index}]"))
    conflicted_refs = {f"inbox.rows[{index}]" for index in matched_rows & note_rows}
    for index in matched_rows & note_rows:
        thread, peer, direction, moment = row_meta[index]
        uncertain.append(DmUnknown(peer, thread, direction, moment, "conflicting_message_kinds", f"inbox.rows[{index}]"))
    verified = [item for item in verified if item.inbox_ref not in conflicted_refs]
    for thread in tainted:
        for peer in peers_by_thread.get(thread, set()):
            uncertain.append(DmUnknown(peer, thread, None, None, "tainted_thread", "inbox.thread"))
    attributed: list[DmUnknown] = []
    for item in uncertain:
        peers = peers_by_thread.get(item.thread or "", set())
        if item.profile_url is None and peers and item.thread not in unidentified_threads:
            attributed.extend(DmUnknown(peer, item.thread, item.direction, item.occurred_at, item.reason, item.source_ref) for peer in sorted(peers))
        else:
            attributed.append(item)
    uncertain = attributed
    verified.sort(key=lambda item: (item.profile_url, item.occurred_at, item.activity_id))
    uncertain.sort(key=lambda item: (item.profile_url or "", item.thread or "", item.source_ref, item.reason))
    return DmEvidence(tuple(verified), tuple(uncertain), tuple(sorted(tainted)), tuple(sorted(set(reasons))), acquired, upstream_freshness)


def _date_added(row: Mapping[str, Any], now: datetime) -> str | None:
    """Preserve the exact prospect creation string only when it is a valid past instant."""
    value = row.get("created_at")
    parsed = _aware_time(value)
    return value if parsed is not None and parsed <= now else None


def _connection_day(value: Any) -> date | None:
    """Read ISO or provider English calendar dates without inventing a time or zone."""
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        match = re.fullmatch(r"(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})", value)
        months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        if match is None or match[2] not in months:
            return None
        return date(int(match[3]), months.index(match[2]) + 1, int(match[1]))
    except ValueError:
        return None


def _saved_draft(research: Mapping[str, Any], profile: str) -> tuple[str, list[dict[str, str]]] | None:
    """Accept one exact-profile saved opener with source URLs for every supporting fact."""
    rows = research.get("rows")
    if not isinstance(rows, list):
        return None
    matches = [row for row in rows if isinstance(row, Mapping) and normalize_profile_url(row.get("profile_url")) == profile]
    if len(matches) != 1:
        return None
    row = matches[0]
    identity = row.get("identity_match")
    opener = row.get("proposed_personalized_opener")
    facts = row.get("supporting_facts")
    if not isinstance(identity, Mapping) or identity.get("canonical_profile_url_exact") is not True or not isinstance(opener, str) or not opener.strip() or not isinstance(facts, list) or not facts:
        return None
    citations: list[dict[str, str]] = []
    for fact in facts:
        if not isinstance(fact, Mapping) or not isinstance(fact.get("fact"), str) or not fact["fact"].strip() or not _citation_url(fact.get("source_url")):
            return None
        citations.append({"fact": fact["fact"], "source_url": fact["source_url"]})
    return opener, citations


def _qualified(policy: Mapping[str, Any], profile: str, connected_on: date, now: datetime) -> tuple[bool, str]:
    """Check cited US PVF fit and any supplied acceptance attestation."""
    profiles = policy.get("profiles")
    if not isinstance(profiles, Mapping):
        return False, "qualification_missing"
    matches = [value for key, value in profiles.items() if normalize_profile_url(key) == profile]
    if len(matches) != 1 or not isinstance(matches[0], Mapping):
        return False, "qualification_identity_ambiguous"
    row = matches[0]
    if row.get("opt_out") is True:
        return False, "opt_out"
    if "accepted_at" in row:
        accepted = _aware_time(row["accepted_at"])
        if accepted is None or accepted > now or accepted.date() != connected_on:
            return False, "acceptance_date_conflicts_with_provider"
    if not 0 <= (now.date() - connected_on).days <= 30:
        return False, "recent_acceptance_unverified"
    citations = row.get("qualification_citations")
    if row.get("country") != "US" or row.get("pvf_employer") is not True or not isinstance(citations, list) or not citations or any(not _citation_url(item) for item in citations):
        return False, "us_pvf_qualification_missing"
    return True, "qualified"


def plan_dm_actions(
    evidence: DmEvidence,
    prospects: Mapping[str, Any],
    connections: Mapping[str, Any],
    research: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Plan manual actions from all positive and uncertain history for each prospect."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("clock_timezone_invalid")
    now = now.astimezone(UTC)
    reasons = list(evidence.coverage_reasons)
    connection_reasons: list[str] = []
    try:
        prospect_rows = _validate_database_snapshot(prospects, "prospects")
    except (ShortlistInputError, TypeError):
        prospect_rows = []
        reasons.append("prospects_incomplete")
    prospects_acquired = _aware_time(prospects.get("completed_at")) if isinstance(prospects, Mapping) else None
    if prospects_acquired is None or prospects_acquired > now or now - prospects_acquired > timedelta(days=1):
        reasons.append("prospects_stale_or_missing_acquisition")
    try:
        connection_rows = _validate_provider_snapshot(connections, "CONNECTIONS")
    except (ShortlistInputError, TypeError):
        connection_rows = []
        connection_reasons.append("connections_incomplete")
    connections_acquired = _aware_time(connections.get("completed_at")) if isinstance(connections, Mapping) else None
    if connections_acquired is None or connections_acquired > now or now - connections_acquired > timedelta(days=1):
        connection_reasons.append("connections_stale_or_missing_acquisition")
    connections_by_profile: dict[str, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for index, connection in enumerate(connection_rows):
        profile = normalize_profile_url(connection.get("URL"))
        if profile:
            connections_by_profile[profile].append((index, connection))
    profiles: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in prospect_rows:
        profile = normalize_profile_url(row.get("linkedin_url"))
        if profile:
            profiles[profile].append(row)
    report: dict[str, Any] = {"reply": [], "follow_up": [], "first_dm": [], "withheld": [], "coverage": {"reasons": sorted(set(reasons + connection_reasons)), "upstream_freshness": evidence.upstream_freshness, "changelog_scope": evidence.changelog_scope}}
    for profile in sorted(profiles):
        rows = profiles[profile]
        if len(rows) != 1:
            report["withheld"].append({"profile_url": profile, "reason": "prospect_identity_ambiguous"})
            continue
        row = rows[0]
        attrs = row.get("attributes")
        if isinstance(attrs, Mapping) and _suppressed(attrs):
            report["withheld"].append({"profile_url": profile, "reason": "opt_out"})
            continue
        policy_profiles = policy.get("profiles") if isinstance(policy, Mapping) else None
        policy_rows = [value for key, value in policy_profiles.items() if normalize_profile_url(key) == profile] if isinstance(policy_profiles, Mapping) else []
        if len(policy_rows) == 1 and isinstance(policy_rows[0], Mapping) and policy_rows[0].get("opt_out") is True:
            report["withheld"].append({"profile_url": profile, "reason": "opt_out"})
            continue
        if len(policy_rows) > 1 or (len(policy_rows) == 1 and (not isinstance(policy_rows[0], Mapping) or policy_rows[0].get("opt_out") is not False)):
            report["withheld"].append({"profile_url": profile, "reason": "policy_opt_out_state_unverified"})
            continue
        messages = [item for item in evidence.verified if item.profile_url == profile]
        unknown = [item for item in evidence.uncertain if item.profile_url in (profile, None)]
        common = {"profile_url": profile, "prospect_id": row["id"], "date_added": _date_added(row, now), "date_added_source": f"prospects.rows[{prospect_rows.index(row)}].created_at"}
        if reasons:
            report["withheld"].append({**common, "reason": "source_coverage_incomplete"})
            continue
        inbound = [item for item in messages if item.direction == "inbound"]
        outbound = [item for item in messages if item.direction == "outbound"]
        latest_in = max(inbound, key=lambda item: item.occurred_at) if inbound else None
        latest_out = max(outbound, key=lambda item: item.occurred_at) if outbound else None
        if latest_in and (not latest_out or latest_in.occurred_at > latest_out.occurred_at):
            blocking = any(item.occurred_at is None or item.occurred_at >= latest_in.occurred_at for item in unknown)
            if not blocking:
                report["reply"].append({**common, "reason": "latest verified inbound DM after outbound", "message_at": latest_in.occurred_at.isoformat(), "message": latest_in.content, "source_refs": [latest_in.inbox_ref, latest_in.changelog_ref], "action": "Owner writes reply", "read_state": "unknown"})
                continue
        if latest_out and now - latest_out.occurred_at >= timedelta(hours=72) and (not latest_in or latest_in.occurred_at < latest_out.occurred_at):
            blocking = any(item.occurred_at is None or item.occurred_at >= latest_out.occurred_at for item in unknown)
            if not blocking:
                report["follow_up"].append({**common, "reason": "verified outbound DM at least 72 hours old with no later observed inbound", "message_at": latest_out.occurred_at.isoformat(), "elapsed_hours": int((now - latest_out.occurred_at).total_seconds() // 3600), "message": latest_out.content, "source_refs": [latest_out.inbox_ref, latest_out.changelog_ref], "action": "Owner writes follow-up", "read_state": "unknown"})
                continue
        if messages or unknown:
            report["withheld"].append({**common, "reason": "message_history_ambiguous_or_action_not_due"})
            continue
        if connection_reasons:
            report["withheld"].append({**common, "reason": "source_coverage_incomplete"})
            continue
        connection_matches = connections_by_profile.get(profile, [])
        if not connection_matches:
            report["withheld"].append({**common, "reason": "current_connection_unverified"})
            continue
        if len(connection_matches) != 1:
            report["withheld"].append({**common, "reason": "connection_identity_ambiguous"})
            continue
        connection_index, connection = connection_matches[0]
        connected_on = _connection_day(connection.get("Connected On"))
        if connected_on is None:
            report["withheld"].append({**common, "reason": "connection_date_unverified"})
            continue
        qualified, reason = _qualified(policy, profile, connected_on, now)
        if not qualified:
            report["withheld"].append({**common, "reason": reason})
            continue
        draft = _saved_draft(research, profile) if isinstance(research, Mapping) else None
        if draft is None:
            report["withheld"].append({**common, "reason": "exact_cited_research_missing"})
            continue
        report["first_dm"].append({**common, "reason": "no observed prior DM in supplied snapshot; consent changelog is bounded", "connected_on": connected_on.isoformat(), "connection_source": f"connections.rows[{connection_index}].Connected On", "connection_date_precision": "calendar_day", "draft": draft[0], "citations": draft[1], "action": "Owner reviews and sends first DM", "read_state": "unknown"})
    return report