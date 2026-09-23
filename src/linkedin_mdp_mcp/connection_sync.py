from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Mapping, Sequence

_LINKEDIN_HOST_RE = re.compile(
    r"^https?://(?:[A-Za-z0-9-]+\.)?linkedin\.com",
    re.IGNORECASE,
)


@dataclass
class ConnectionSyncPlan:
    fetched: int
    matched: int
    unmatched: int
    events: list[dict[str, Any]]


def normalize_linkedin_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None

    normalized = re.sub(r"[?#].*$", "", normalized)
    normalized = _LINKEDIN_HOST_RE.sub("https://www.linkedin.com", normalized, count=1)
    normalized = re.sub(r"/+$", "", normalized)

    if not normalized.startswith("https://www.linkedin.com/"):
        return None
    return normalized


def _event_timestamp(
    connected_on: Any,
    *,
    observed_at: datetime,
) -> tuple[str, str]:
    if isinstance(connected_on, str):
        try:
            connected_date = date.fromisoformat(connected_on.strip())
        except ValueError:
            pass
        else:
            occurred_at = datetime.combine(
                connected_date,
                time.min,
                tzinfo=timezone.utc,
            )
            return occurred_at.isoformat(), "event_date_day_precision"

    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return observed_at.astimezone(timezone.utc).isoformat(), "observed_at"


def plan_connection_events(
    rows: Sequence[Any],
    prospects_by_key: Mapping[str, str],
    *,
    observed_at: datetime,
) -> ConnectionSyncPlan:
    events_by_key: dict[str, dict[str, Any]] = {}
    matched = 0
    unmatched = 0

    for row in rows:
        if not isinstance(row, dict):
            unmatched += 1
            continue

        linkedin_url = normalize_linkedin_url(row.get("URL"))
        prospect_id = prospects_by_key.get(linkedin_url) if linkedin_url else None
        if prospect_id is None:
            unmatched += 1
            continue

        matched += 1
        external_key = f"connection:{linkedin_url}"
        if external_key in events_by_key:
            continue

        occurred_at, timestamp_semantics = _event_timestamp(
            row.get("Connected On"),
            observed_at=observed_at,
        )
        events_by_key[external_key] = {
            "prospect_id": prospect_id,
            "source": "LINKEDIN_MDP",
            "event_type": "LINKEDIN_CONNECTION_FOUND",
            "external_key": external_key,
            "occurred_at": occurred_at,
            "payload": {
                "raw": dict(row),
                "timestamp_semantics": timestamp_semantics,
            },
        }

    return ConnectionSyncPlan(
        fetched=len(rows),
        matched=matched,
        unmatched=unmatched,
        events=list(events_by_key.values()),
    )
