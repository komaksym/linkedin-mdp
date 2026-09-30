"""Reconcile historical outbound invitation evidence for existing prospects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .connection_sync import normalize_linkedin_url


class InvitationSyncError(RuntimeError):
    """Raised when an INVITATIONS snapshot cannot be reconciled safely."""


@dataclass
class InvitationSyncPlan:
    """Duplicate-safe events and aggregate counts from a complete provider snapshot."""

    fetched_rows: int
    inbound_rows: int
    unmatched_rows: int
    malformed_rows: int
    matched_rows: int
    unique_matched: int
    events: list[dict[str, Any]]


@dataclass
class InvitationSyncSummary:
    """Aggregate result of one historical invitation reconciliation."""

    fetched_rows: int
    inbound_rows: int
    unmatched_rows: int
    malformed_rows: int
    matched_rows: int
    unique_matched: int
    inserted: int
    already_present: int


def _provider_timestamp(value: Any) -> tuple[str, str, str] | None:
    """Normalize supported provider text without assigning a timezone to local times."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.strptime(raw, "%m/%d/%y, %I:%M %p")
    except ValueError:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if "T" not in raw and " " not in raw:
            return None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.isoformat(), "provider_local_time_timezone_unspecified", raw
    return parsed.astimezone(timezone.utc).isoformat(), "provider_absolute_time", raw


def plan_invitation_events(
    rows: Sequence[Any],
    prospects_by_key: Mapping[str, str],
    *,
    observed_at: datetime,
) -> InvitationSyncPlan:
    """Plan only timestamped outbound events for already known canonical profiles."""
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    observed = observed_at.astimezone(timezone.utc).isoformat()
    events_by_key: dict[str, dict[str, Any]] = {}
    inbound_rows = unmatched_rows = malformed_rows = matched_rows = 0

    for row in rows:
        if not isinstance(row, dict):
            malformed_rows += 1
            continue
        if row.get("Direction") == "INCOMING":
            inbound_rows += 1
            continue
        if row.get("Direction") != "OUTGOING":
            malformed_rows += 1
            continue
        url = normalize_linkedin_url(row.get("inviteeProfileUrl"))
        if url is None:
            malformed_rows += 1
            continue
        timestamp = _provider_timestamp(row.get("Sent At"))
        if timestamp is None:
            malformed_rows += 1
            continue
        prospect_id = prospects_by_key.get(url)
        if prospect_id is None:
            unmatched_rows += 1
            continue

        matched_rows += 1
        normalized, semantics, original = timestamp
        key = f"invitation-history:{url}:{normalized}"
        if key in events_by_key:
            continue
        events_by_key[key] = {
            "prospect_id": prospect_id,
            "source": "LINKEDIN_MDP",
            "event_type": "LINKEDIN_INVITATION_HISTORY_FOUND",
            "external_key": key,
            "occurred_at": normalized
            if semantics == "provider_absolute_time"
            else observed,
            "payload": {
                "raw": dict(row),
                "provider_timestamp": original,
                "normalized_provider_timestamp": normalized,
                "timestamp_semantics": "actual"
                if semantics == "provider_absolute_time"
                else "observed_at",
                "provider_timestamp_semantics": (
                    "absolute_time"
                    if semantics == "provider_absolute_time"
                    else "local_time_timezone_unspecified"
                ),
                "canonical_url": url,
                "observed_via": "INVITATIONS",
                "lifecycle_state": "unknown",
            },
        }

    return InvitationSyncPlan(
        fetched_rows=len(rows),
        inbound_rows=inbound_rows,
        unmatched_rows=unmatched_rows,
        malformed_rows=malformed_rows,
        matched_rows=matched_rows,
        unique_matched=len(events_by_key),
        events=list(events_by_key.values()),
    )


def _complete_rows(snapshot: Any) -> list[Any]:
    """Reject incomplete snapshots and raw envelopes the generic client may skip."""
    if not isinstance(snapshot, dict):
        raise InvitationSyncError("INVITATIONS snapshot must be an object")
    if snapshot.get("truncated") is not False:
        raise InvitationSyncError("INVITATIONS snapshot is truncated")
    pages = snapshot.get("page_count")
    if isinstance(pages, bool) or not isinstance(pages, int) or pages < 1:
        raise InvitationSyncError("INVITATIONS snapshot did not return a provider page")
    elements = snapshot.get("raw_elements")
    if not isinstance(elements, list):
        raise InvitationSyncError(
            "INVITATIONS snapshot has missing or malformed provider pages"
        )
    for element in elements:
        if (
            not isinstance(element, dict)
            or element.get("snapshotDomain") != "INVITATIONS"
            or not isinstance(element.get("snapshotData"), list)
        ):
            raise InvitationSyncError(
                "INVITATIONS snapshot has a malformed raw page envelope"
            )
    rows = snapshot.get("rows")
    if not isinstance(rows, list):
        raise InvitationSyncError("INVITATIONS snapshot did not return a rows list")
    return rows


async def reconcile_invitations(
    linkedin: Any,
    supabase: Any,
    *,
    observed_at: datetime | None = None,
) -> InvitationSyncSummary:
    """Fetch and validate all provider pages before any database read or write."""
    snapshot = await linkedin.snapshot(
        "INVITATIONS", max_pages=50, strict_elements=True
    )
    rows = _complete_rows(snapshot)
    observed_at = observed_at or datetime.now(timezone.utc)
    prospects_by_key = await supabase.prospect_ids_by_linkedin_key()
    plan = plan_invitation_events(rows, prospects_by_key, observed_at=observed_at)
    inserted = await supabase.insert_events_ignore_duplicates(plan.events)
    if (
        isinstance(inserted, bool)
        or not isinstance(inserted, int)
        or not 0 <= inserted <= plan.unique_matched
    ):
        raise InvitationSyncError("Supabase returned an invalid inserted-event count")
    return InvitationSyncSummary(
        fetched_rows=plan.fetched_rows,
        inbound_rows=plan.inbound_rows,
        unmatched_rows=plan.unmatched_rows,
        malformed_rows=plan.malformed_rows,
        matched_rows=plan.matched_rows,
        unique_matched=plan.unique_matched,
        inserted=inserted,
        already_present=plan.unique_matched - inserted,
    )
