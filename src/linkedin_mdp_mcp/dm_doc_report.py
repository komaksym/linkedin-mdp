"""Validate private DM publication inputs and render the two managed Doc bodies."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from linkedin_mdp_mcp.dm_actions import _aware_time, _citation_url
from linkedin_mdp_mcp.google_doc_report import BEGIN_MARKER, END_MARKER

_WARSAW = ZoneInfo("Europe/Warsaw")
_FIRST_DM_ACTION = "Owner reviews and sends first DM"
_FOLLOW_UP_ACTION = "Owner writes follow-up"
_REPLY_ACTION = "Owner writes reply"
_CONDITIONAL_STATUS = "CONDITIONAL FIRST-DM DRAFT. NOT CLEARED TO SEND"
_PLANNER_KEYS = {"reply", "follow_up", "first_dm", "withheld", "coverage"}


class DmDocReportError(ValueError):
    """Represent invalid private DM publication input without exposing its contents."""


@dataclass(frozen=True)
class DmDocBodies:
    """Hold the two validated report bodies for their separate Doc publishers."""

    first_dm: str
    follow_up: str


@dataclass(frozen=True)
class ConditionalFirstDmDraft:
    """Hold one explicitly retained, uncleared researched first-DM draft."""

    name: str
    profile_url: str
    date_added: str
    connected_on: str
    draft: str
    employer_source_url: str


def build_dm_doc_bodies(actions_value: Any, retained_value: Any, *, now: datetime) -> DmDocBodies:
    """Validate both private inputs before rendering either Google Doc body."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise DmDocReportError("clock_timezone_invalid")
    actions = _actions(actions_value)
    retained_rows, publication_status = _retained_evidence(retained_value, actions)
    first_dm = _render_first_dm(actions, retained_rows, publication_status, now)
    follow_up = _render_follow_up(actions, now)
    if any(marker in body for body in (first_dm, follow_up) for marker in (BEGIN_MARKER, END_MARKER)):
        raise DmDocReportError("managed_marker_in_body")
    return DmDocBodies(first_dm=first_dm, follow_up=follow_up)


def _actions(value: Any) -> dict[str, Any]:
    """Require the exact planner envelope and validate every planner row."""
    if not isinstance(value, dict) or set(value) != _PLANNER_KEYS:
        raise DmDocReportError("actions_shape_invalid")
    for key in ("reply", "follow_up", "first_dm", "withheld"):
        if not isinstance(value[key], list) or any(not isinstance(row, dict) for row in value[key]):
            raise DmDocReportError("actions_shape_invalid")
    _coverage(value["coverage"])
    for row in value["reply"]:
        _message_row(row, action=_REPLY_ACTION, elapsed=False)
    for row in value["follow_up"]:
        _message_row(row, action=_FOLLOW_UP_ACTION, elapsed=True)
    for row in value["first_dm"]:
        _first_dm_row(row)
    for row in value["withheld"]:
        _withheld_row(row)
    return value


def _coverage(value: Any) -> None:
    """Validate the planner coverage block used by both report headers."""
    if not isinstance(value, dict) or set(value) != {"reasons", "upstream_freshness", "changelog_scope"}:
        raise DmDocReportError("coverage_shape_invalid")
    reasons = value["reasons"]
    if not isinstance(reasons, list) or any(not isinstance(reason, str) or not reason for reason in reasons):
        raise DmDocReportError("coverage_shape_invalid")
    if any(not isinstance(value[key], str) for key in ("upstream_freshness", "changelog_scope")):
        raise DmDocReportError("coverage_shape_invalid")


def _text(row: Mapping[str, Any], key: str) -> str:
    """Return one required nonempty string field."""
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DmDocReportError("row_field_invalid")
    return value


def _optional_text(row: Mapping[str, Any], key: str) -> str | None:
    """Return one optional string field."""
    value = row.get(key)
    if value is not None and not isinstance(value, str):
        raise DmDocReportError("row_field_invalid")
    return value


def _common_message_fields(row: Mapping[str, Any]) -> None:
    """Validate fields shared by planner reply and follow-up rows."""
    for key in ("profile_url", "prospect_id", "date_added_source", "reason", "message"):
        _text(row, key)
    _optional_text(row, "date_added")
    if _aware_time(row.get("message_at")) is None:
        raise DmDocReportError("row_field_invalid")
    source_refs = row.get("source_refs")
    if not isinstance(source_refs, list) or len(source_refs) != 2 or any(not isinstance(ref, str) or not ref for ref in source_refs):
        raise DmDocReportError("row_field_invalid")
    _text(row, "read_state")


def _message_row(row: Mapping[str, Any], *, action: str, elapsed: bool) -> None:
    """Validate one planner reply or follow-up row."""
    expected = {
        "profile_url", "prospect_id", "date_added", "date_added_source", "reason",
        "message_at", "message", "source_refs", "action", "read_state",
    }
    if elapsed:
        expected.add("elapsed_hours")
    if set(row) != expected:
        raise DmDocReportError("row_shape_invalid")
    _common_message_fields(row)
    if _text(row, "action") != action:
        raise DmDocReportError("row_field_invalid")
    if elapsed:
        hours = row.get("elapsed_hours")
        if not isinstance(hours, int) or isinstance(hours, bool) or hours < 0:
            raise DmDocReportError("row_field_invalid")


def _first_dm_row(row: Mapping[str, Any]) -> None:
    """Validate one planner first-DM row."""
    if set(row) != {
        "profile_url", "prospect_id", "date_added", "date_added_source", "reason",
        "connected_on", "connection_source", "connection_date_precision", "draft",
        "citations", "action", "read_state",
    }:
        raise DmDocReportError("row_shape_invalid")
    for key in ("profile_url", "prospect_id", "date_added_source", "reason", "connection_source", "draft"):
        _text(row, key)
    _optional_text(row, "date_added")
    if _text(row, "connection_date_precision") != "calendar_day":
        raise DmDocReportError("row_field_invalid")
    try:
        date.fromisoformat(_text(row, "connected_on"))
    except ValueError as exc:
        raise DmDocReportError("row_field_invalid") from exc
    _citations(row.get("citations"))
    if _text(row, "action") != _FIRST_DM_ACTION:
        raise DmDocReportError("row_field_invalid")
    _text(row, "read_state")


def _citations(value: Any) -> list[dict[str, str]]:
    """Validate cited first-DM research and return a normalized copy."""
    if not isinstance(value, list) or not value:
        raise DmDocReportError("citation_shape_invalid")
    checked: list[dict[str, str]] = []
    for citation in value:
        if not isinstance(citation, dict) or set(citation) != {"fact", "source_url"}:
            raise DmDocReportError("citation_shape_invalid")
        fact = _text(citation, "fact")
        source_url = _text(citation, "source_url")
        if not _citation_url(source_url):
            raise DmDocReportError("citation_url_invalid")
        checked.append({"fact": fact, "source_url": source_url})
    return checked


def _withheld_row(row: Mapping[str, Any]) -> None:
    """Validate the planner fields that the first-DM report publishes for withheld rows."""
    _text(row, "profile_url")
    _text(row, "reason")


def _retained_evidence(
    value: Any,
    actions: Mapping[str, Any],
) -> tuple[tuple[ConditionalFirstDmDraft, ...], str]:
    """Require retained publication counts to match the current planner artifact."""
    if not isinstance(value, dict) or "publication" not in value or "rows" not in value:
        raise DmDocReportError("retained_shape_invalid")
    publication = value["publication"]
    rows = value["rows"]
    if not isinstance(publication, dict) or not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise DmDocReportError("retained_shape_invalid")
    expected = {
        "cleared_first_dms": len(actions["first_dm"]),
        "conditional_researched_drafts": len(rows),
        "strict_planner_withheld_rows": len(actions["withheld"]),
    }
    for key, count in expected.items():
        value_count = publication.get(key)
        if not isinstance(value_count, int) or isinstance(value_count, bool) or value_count != count:
            raise DmDocReportError("retained_evidence_stale")
    status = publication.get("status")
    if not isinstance(status, str) or not status.strip():
        raise DmDocReportError("retained_shape_invalid")
    checked = tuple(_retained_row(row) for row in rows)
    return checked, status


def _retained_row(row: Mapping[str, Any]) -> ConditionalFirstDmDraft:
    """Parse one exact retained verification row into the publication domain type."""
    if set(row) != {
        "name",
        "profile_url",
        "date_added",
        "connected_on",
        "draft",
        "status",
        "employer_source_url",
    }:
        raise DmDocReportError("retained_row_shape_invalid")
    if _text(row, "status") != _CONDITIONAL_STATUS:
        raise DmDocReportError("retained_evidence_stale")
    connected_on = _text(row, "connected_on")
    try:
        date.fromisoformat(connected_on)
    except ValueError as exc:
        raise DmDocReportError("retained_row_field_invalid") from exc
    source = _text(row, "employer_source_url")
    if not _citation_url(source):
        raise DmDocReportError("retained_row_field_invalid")
    return ConditionalFirstDmDraft(
        name=_text(row, "name"),
        profile_url=_text(row, "profile_url"),
        date_added=_text(row, "date_added"),
        connected_on=connected_on,
        draft=_text(row, "draft"),
        employer_source_url=source,
    )


def _status(actions: Mapping[str, Any]) -> str:
    """Map source coverage to the publishable report status."""
    return "BLOCKED" if actions["coverage"]["reasons"] else "COMPLETE"


def _header(title: str, actions: Mapping[str, Any], now: datetime) -> list[str]:
    """Render common status and source-coverage lines."""
    coverage = actions["coverage"]
    return [
        title,
        f"Run time: {now.astimezone(_WARSAW):%Y-%m-%d %H:%M:%S %Z}",
        f"Status: {_status(actions)}",
        f"Changelog scope: {coverage['changelog_scope']}",
        f"Upstream freshness: {coverage['upstream_freshness']}",
        f"Withholding reasons: {', '.join(coverage['reasons']) or 'none'}",
    ]


def _render_first_dm(
    actions: Mapping[str, Any],
    retained_rows: tuple[ConditionalFirstDmDraft, ...],
    publication_status: str,
    now: datetime,
) -> str:
    """Render cleared, conditional, and strictly withheld first-DM evidence."""
    lines = _header("LinkedIn first-time DM report", actions, now)
    lines.extend((
        f"First-DM drafts (owner review required): {len(actions['first_dm'])}",
        f"Cleared first-DM count: {len(actions['first_dm'])}",
        f"Conditional retained count: {len(retained_rows)}",
        f"Strict planner withheld count: {len(actions['withheld'])}",
        f"Withheld rows: {len(actions['withheld'])}",
        f"Publication status: {publication_status}",
        "Next action: review cleared planner rows manually; retained conditional rows remain uncleared.",
        "",
        "Retained conditional rows (uncleared)",
        "",
    ))
    for retained_row in retained_rows:
        lines.extend((
            f"- {retained_row.name}",
            f"  Profile: {retained_row.profile_url}",
            f"  Status: {_CONDITIONAL_STATUS}",
            "  Cleared to send: no",
            f"  Connected on: {retained_row.connected_on}",
            f"  Date added: {retained_row.date_added}",
            f"  Saved researched draft: {retained_row.draft}",
            f"  Research source: {retained_row.employer_source_url}",
            "",
        ))
    lines.extend(("Planner first-DM rows", ""))
    for planner_row in actions["first_dm"]:
        lines.extend((
            f"- {planner_row['profile_url']}",
            f"  Connected on: {planner_row['connected_on']} (calendar day)",
            f"  Connection source: {planner_row['connection_source']}",
            f"  Date added: {planner_row['date_added'] or 'unknown'}",
            f"  Saved researched draft: {planner_row['draft']}",
        ))
        for citation in planner_row["citations"]:
            lines.append(f"  Research source: {citation['source_url']} ({citation['fact']})")
        lines.extend((
            f"  Read state: {planner_row['read_state']}",
            f"  Action: {planner_row['action']}",
            f"  Note: {planner_row['reason']}",
            "",
        ))
    lines.extend(("Withheld rows", ""))
    for withheld_row in actions["withheld"]:
        lines.extend((f"- {withheld_row['profile_url']}", f"  Reason: {withheld_row['reason']}", ""))
    return "\n".join(lines) + "\n"


def _render_follow_up(actions: Mapping[str, Any], now: datetime) -> str:
    """Render evidence reminders without generating any follow-up copy."""
    lines = _header("LinkedIn follow-up DM report", actions, now)
    lines.extend((
        f"Due follow-ups: {len(actions['follow_up'])}",
        f"Withheld rows: {len(actions['withheld'])}",
        "No follow-up text was generated.",
        "Next action: write each follow-up manually after reviewing the evidence below.",
        "",
        "Follow-up rows",
        "",
    ))
    for row in actions["follow_up"]:
        moment = _aware_time(row["message_at"])
        if moment is None:
            raise DmDocReportError("row_field_invalid")
        lines.extend((
            f"- {row['profile_url']}",
            f"  Previous outbound at: {moment.astimezone(_WARSAW):%Y-%m-%d %H:%M:%S %Z} (elapsed {row['elapsed_hours']} hours)",
            f"  Verified previous message: {row['message']}",
            f"  Message sources: {', '.join(row['source_refs'])}",
            f"  Read state: {row['read_state']}",
            f"  Action: {row['action']}",
            "",
        ))
    return "\n".join(lines) + "\n"
