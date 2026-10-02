from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit


class InboxSyncError(RuntimeError):
    """Raised when an INBOX snapshot cannot be reconciled safely."""


@dataclass(frozen=True)
class InboxSyncPlan:
    """Hold duplicate-safe inbox evidence and aggregate skip counts."""

    fetched_rows: int
    planned_events: int
    skipped_rows: int
    withheld_thread_rows: int
    skip_reasons: dict[str, int]
    events: list[dict[str, Any]]


@dataclass(frozen=True)
class InboxSyncSummary:
    """Describe one inbox plan and whether its event batch was applied."""

    fetched_rows: int
    planned_events: int
    skipped_rows: int
    withheld_thread_rows: int
    skip_reasons: dict[str, int]
    inserted: int | None
    already_present: int | None
    dry_run: bool


_PROFILE_HOSTS = {"linkedin.com", "www.linkedin.com"}
_REGIONAL_HOST = re.compile(r"^[a-z]{2}\.linkedin\.com$", re.IGNORECASE)
_PROFILE_SLUG = re.compile(r"^[A-Za-z0-9._~-]+$")
_ATTACHMENT_KEY = re.compile(r"attachment", re.IGNORECASE)


def normalize_profile_url(value: Any) -> str | None:
    """Canonicalize a supported LinkedIn member profile URL or reject it."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname or ""
        if parsed.scheme.lower() not in {"http", "https"}:
            return None
        if parsed.username is not None or parsed.password is not None or parsed.port is not None:
            return None
    except ValueError:
        return None
    if host.lower() not in _PROFILE_HOSTS and not _REGIONAL_HOST.fullmatch(host):
        return None
    profile_path = parsed.path[:-1] if parsed.path.endswith("/") and not parsed.path.endswith("//") else parsed.path
    parts = profile_path.split("/")
    if (
        len(parts) != 3
        or parts[0]
        or parts[1].lower() != "in"
        or parts[2] in {".", ".."}
        or not _PROFILE_SLUG.fullmatch(parts[2])
    ):
        return None
    return f"https://www.linkedin.com/in/{parts[2]}"


def _timestamp(value: Any, observed_at: datetime) -> tuple[str, str, str, bool, str] | None:
    """Parse provider time without assigning a timezone to naive local text."""
    if not isinstance(value, str) or not value.strip():
        return None
    source = value.strip()
    if "T" not in source and " " not in source:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC", source):
            parsed = datetime.strptime(source, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=UTC)
        else:
            parsed = datetime.fromisoformat(source)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return observed_at.astimezone(UTC).isoformat(), source, "observed_at", True, parsed.isoformat()
    normalized = parsed.astimezone(UTC).isoformat()
    return normalized, source, "actual", False, normalized


def _attachments(row: Mapping[str, Any]) -> dict[str, Any]:
    """Retain provider fields whose names identify message attachments."""
    return {
        key: value
        for key, value in row.items()
        if _ATTACHMENT_KEY.search(key) and value not in (None, "", [], {})
    }


def _participants(row: Any) -> tuple[str | None, set[str], bool]:
    """Extract canonical participants and mark malformed participant evidence."""
    if not isinstance(row, dict):
        return None, set(), True
    conversation = row.get("CONVERSATION ID")
    if not isinstance(conversation, str) or not conversation.strip():
        return None, set(), True
    sender = normalize_profile_url(row.get("SENDER PROFILE URL"))
    recipients = row.get("RECIPIENT PROFILE URLS")
    if isinstance(recipients, str):
        recipients = [recipients]
    if sender is None or not isinstance(recipients, list) or not recipients:
        return conversation.strip(), set(), True
    canonical_recipients = [normalize_profile_url(value) for value in recipients]
    participants = {sender, *(value for value in canonical_recipients if value is not None)}
    malformed = any(value is None for value in canonical_recipients)
    return conversation.strip(), participants, malformed


def _safe_one_to_one_row(row: Any, account: str) -> bool:
    """Require one distinct sender and recipient with the account as a participant."""
    if not isinstance(row, dict):
        return False
    sender = normalize_profile_url(row.get("SENDER PROFILE URL"))
    recipients = row.get("RECIPIENT PROFILE URLS")
    if isinstance(recipients, str):
        recipients = [recipients]
    if sender is None or not isinstance(recipients, list) or len(recipients) != 1:
        return False
    recipient = normalize_profile_url(recipients[0])
    return recipient is not None and sender != recipient and account in {sender, recipient}


def _event_key(
    conversation: str,
    direction: str,
    sender: str,
    peer: str,
    timestamp: str,
    content: str,
    subject: str,
    attachments: Mapping[str, Any],
) -> str:
    """Hash stable message identity fields, including attachment-only evidence."""
    identity = json.dumps(
        [conversation, direction, sender, peer, timestamp, content, subject, attachments],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "message:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def plan_inbox_events(
    rows: Sequence[Any],
    prospects_by_key: Mapping[str, str],
    *,
    account_profile_url: str,
    observed_at: datetime,
) -> InboxSyncPlan:
    """Plan exact 1:1 message evidence from a complete provider snapshot."""
    account = normalize_profile_url(account_profile_url)
    if account is None:
        raise ValueError("account_profile_url must be a supported LinkedIn /in/ URL")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")

    canonical_prospects: dict[str, str] = {}
    conflicted_prospects: set[str] = set()
    for profile_url, candidate_id in prospects_by_key.items():
        canonical_url = normalize_profile_url(profile_url)
        if canonical_url is None or not isinstance(candidate_id, str) or not candidate_id:
            continue
        existing_id = canonical_prospects.get(canonical_url)
        if existing_id is not None and existing_id != candidate_id:
            conflicted_prospects.add(canonical_url)
        else:
            canonical_prospects[canonical_url] = candidate_id

    thread_participants: dict[str, set[str]] = {}
    tainted_threads: set[str] = set()
    for row in rows:
        conversation, participants, malformed = _participants(row)
        if conversation is None:
            continue
        if malformed or not _safe_one_to_one_row(row, account):
            tainted_threads.add(conversation)
        thread_participants.setdefault(conversation, set()).update(participants)
        if len(thread_participants[conversation]) > 2:
            tainted_threads.add(conversation)

    events_by_key: dict[str, dict[str, Any]] = {}
    skip_reasons: dict[str, int] = {}
    withheld_thread_rows = 0

    def skip(reason: str) -> None:
        """Increment one fixed aggregate reason without retaining row data."""
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

    for row in rows:
        conversation, participants, malformed = _participants(row)
        if conversation is not None and conversation in tainted_threads:
            withheld_thread_rows += 1
            skip("withheld_thread")
            continue
        if not isinstance(row, dict) or conversation is None:
            skip("malformed_participants")
            continue
        if malformed or len(participants) != 2:
            skip("malformed_participants")
            continue
        sender = normalize_profile_url(row.get("SENDER PROFILE URL"))
        recipients = row.get("RECIPIENT PROFILE URLS")
        if isinstance(recipients, str):
            recipients = [recipients]
        recipient = normalize_profile_url(recipients[0]) if isinstance(recipients, list) and len(recipients) == 1 else None
        if sender is None or recipient is None:
            skip("malformed_participants")
            continue
        if sender == account and recipient != account:
            direction = "outbound"
            peer = recipient
        elif recipient == account and sender != account:
            direction = "inbound"
            peer = sender
        else:
            skip("self_or_ambiguous_direction")
            continue
        prospect_id = canonical_prospects.get(peer)
        if prospect_id is None or peer in conflicted_prospects:
            skip("unknown_prospect")
            continue
        content = row.get("CONTENT", "")
        subject_value = row.get("SUBJECT")
        subject: str = subject_value if isinstance(subject_value, str) else ""
        attachments = _attachments(row)
        if not isinstance(content, str) or (not content.strip() and not attachments):
            skip("empty_content")
            continue
        parsed_time = _timestamp(row.get("DATE"), observed_at)
        if parsed_time is None:
            skip("malformed_timestamp")
            continue
        occurred_at, provider_timestamp, timestamp_semantics, local_unspecified, identity_timestamp = parsed_time
        external_key = _event_key(
            conversation,
            direction,
            sender,
            peer,
            identity_timestamp,
            content,
            subject.strip(),
            attachments,
        )
        if external_key in events_by_key:
            continue
        payload: dict[str, Any] = {
            "conversation_id": conversation,
            "message_kind": "unknown",
            "direction": direction,
            "sender_profile_url": sender,
            "peer_profile_url": peer,
            "provider_timestamp": provider_timestamp,
            "timestamp_semantics": timestamp_semantics,
            "content": content,
            "subject": subject,
            "attachments": attachments,
            "read_state": "unknown",
            "raw": dict(row),
        }
        if local_unspecified:
            payload["provider_local_time_timezone_unspecified"] = True
        events_by_key[external_key] = {
            "prospect_id": prospect_id,
            "source": "LINKEDIN_MDP",
            "event_type": "LINKEDIN_MESSAGE_OBSERVED",
            "external_key": external_key,
            "occurred_at": occurred_at,
            "payload": payload,
        }

    return InboxSyncPlan(
        fetched_rows=len(rows),
        planned_events=len(events_by_key),
        skipped_rows=sum(skip_reasons.values()),
        withheld_thread_rows=withheld_thread_rows,
        skip_reasons=skip_reasons,
        events=list(events_by_key.values()),
    )


async def reconcile_inbox(
    linkedin: Any,
    supabase: Any,
    *,
    account_profile_url: str,
    observed_at: datetime | None = None,
    dry_run: bool = True,
) -> InboxSyncSummary:
    """Validate a complete INBOX snapshot, plan evidence, then optionally apply it."""
    if normalize_profile_url(account_profile_url) is None:
        raise InboxSyncError("account profile URL is invalid")
    observed_at = observed_at or datetime.now(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise InboxSyncError("observation time must be timezone-aware")
    try:
        snapshot = await linkedin.snapshot("INBOX", max_pages=50, strict_elements=True)
    except Exception as exc:
        raise InboxSyncError("LinkedIn INBOX snapshot is unavailable") from exc
    if not isinstance(snapshot, dict) or snapshot.get("truncated") is not False:
        raise InboxSyncError("LinkedIn INBOX snapshot is incomplete")
    if snapshot.get("source_result") in ("not_found", "unavailable"):
        raise InboxSyncError("LinkedIn INBOX snapshot source is unavailable")
    page_count = snapshot.get("page_count")
    if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1:
        raise InboxSyncError("LinkedIn INBOX snapshot returned no provider page")
    rows = snapshot.get("rows")
    raw_elements = snapshot.get("raw_elements")
    if not isinstance(rows, list) or not isinstance(raw_elements, list):
        raise InboxSyncError("LinkedIn INBOX snapshot has an invalid envelope")
    for element in raw_elements:
        if not isinstance(element, dict):
            raise InboxSyncError("LinkedIn INBOX snapshot contains a malformed element")
        if element.get("snapshotDomain") != "INBOX" or not isinstance(element.get("snapshotData"), list):
            raise InboxSyncError("LinkedIn INBOX snapshot contains an invalid domain payload")
        if element.get("source_result") in ("not_found", "unavailable"):
            raise InboxSyncError("LinkedIn INBOX snapshot source is unavailable")

    prospects = await supabase.prospect_ids_by_linkedin_key()
    plan = plan_inbox_events(
        rows,
        prospects,
        account_profile_url=account_profile_url,
        observed_at=observed_at,
    )
    inserted: int | None = None
    already_present: int | None = None
    if not dry_run:
        inserted = await supabase.insert_events_ignore_duplicates(plan.events)
        if isinstance(inserted, bool) or not isinstance(inserted, int) or not 0 <= inserted <= plan.planned_events:
            raise InboxSyncError("Supabase returned an invalid inserted-event count")
        already_present = plan.planned_events - inserted
    return InboxSyncSummary(
        fetched_rows=plan.fetched_rows,
        planned_events=plan.planned_events,
        skipped_rows=plan.skipped_rows,
        withheld_thread_rows=plan.withheld_thread_rows,
        skip_reasons=plan.skip_reasons,
        inserted=inserted,
        already_present=already_present,
        dry_run=dry_run,
    )
