"""Combine validated private recommendation outputs without acquiring or changing actions."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from linkedin_mdp_mcp.google_doc_report import validate_report_text
from linkedin_mdp_mcp.inbox_sync import normalize_profile_url


class CombinedReportError(ValueError):
    """Identify malformed or inconsistent upstream private report data."""


def _json_contract(value: Any) -> None:
    """Reject non-JSON and nonfinite values in every part of an upstream output."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _json_contract(item)
        return
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _json_contract(item)
        return
    raise CombinedReportError("report_json_invalid")


def _object(value: Any) -> Mapping[str, Any]:
    """Require a JSON object at a report boundary."""
    if not isinstance(value, Mapping):
        raise CombinedReportError("report_shape_invalid")
    return value


def _rows(value: Any) -> list[Mapping[str, Any]]:
    """Require a list of JSON objects without silently dropping a row."""
    if not isinstance(value, list) or not all(isinstance(row, Mapping) for row in value):
        raise CombinedReportError("report_rows_invalid")
    return value


def _string(value: Any) -> str:
    """Require a nonempty string where an identity or action field is mandatory."""
    if not isinstance(value, str) or not value.strip():
        raise CombinedReportError("report_field_invalid")
    return value


def _counts(value: Any) -> Mapping[str, int]:
    """Require upstream reason counts to be nonnegative integers."""
    counts = _object(value)
    if any(not isinstance(key, str) or not key or type(count) is not int or count < 0 for key, count in counts.items()):
        raise CombinedReportError("report_counts_invalid")
    return counts


def _profile(row: Mapping[str, Any]) -> str:
    """Require a canonical exact LinkedIn identity for conflict detection."""
    value = _string(row.get("profile_url"))
    if normalize_profile_url(value) != value:
        raise CombinedReportError("profile_identity_invalid")
    return value


def _show(value: Any) -> str:
    """Render an observed scalar or an explicit unknown without inventing data."""
    if value is None:
        return "unknown"
    if isinstance(value, str):
        return value or "unknown"
    if isinstance(value, (bool, int, float)):
        return json.dumps(value, ensure_ascii=False)
    raise CombinedReportError("report_field_invalid")


def _citations(value: Any, *, key: str) -> list[str]:
    """Read each upstream citation URL while retaining its dated provenance."""
    links = []
    for row in _rows(value):
        url = _string(row.get(key))
        detail = ", ".join(f"{name}={_show(row.get(name))}" for name in ("fact", "source_date", "retrieved_at") if name in row)
        links.append(f"{url} ({detail})" if detail else url)
    return links


def _invitation_lines(row: Mapping[str, Any], rank: int) -> list[str]:
    """Keep one upstream priority, provenance, evidence, and unknown set together."""
    profile = _profile(row)
    name = _string(row.get("name"))
    source = _object(row.get("date_added_source"))
    factors = _object(row.get("score_factors"))
    unknown = _object(row.get("unknown_factors"))
    evidence = _object(row.get("evidence"))
    lines = [
        f"{rank}. {name} | {profile}",
        f"Role and company: {_string(row.get('role'))} at {_string(row.get('company_name'))}",
        f"Priority score: {_show(row.get('score'))} ({_string(row.get('score_version'))}); heuristic, not acceptance probability",
        f"Reason: {_string(row.get('reason'))}",
        f"Date added: {_show(row.get('date_added'))} | prospect: {_show(source.get('prospect_id'))} | source: {_show(source.get('source_ref'))} | unknown reason: {_show(source.get('reason'))}",
    ]
    for key, raw in factors.items():
        factor = _object(raw)
        links = _citations(factor.get("citations"), key="url")
        if not links:
            raise CombinedReportError("factor_evidence_missing")
        lines.append(f"Factor {key}: value={_show(factor.get('value'))}, points={_show(factor.get('points'))}, observed={_show(factor.get('observed_at'))}; sources: {', '.join(links) or 'unknown'}")
    for key, reason in unknown.items():
        lines.append(f"Unknown factor {key}: {_show(reason)}")
    for key, raw in evidence.items():
        lines.append(f"Qualification evidence {key}: {', '.join(_citations(raw, key='url')) or 'unknown'}")
    lines.extend((f"Action: {_string(row.get('action'))}", ""))
    return lines


def _dm_lines(row: Mapping[str, Any], kind: str) -> list[str]:
    """Distinguish manual reminders from a saved, cited first-DM draft."""
    lines = [
        f"{_profile(row)} | prospect: {_show(row.get('prospect_id'))}",
        f"Reason: {_string(row.get('reason'))}",
        f"Date added: {_show(row.get('date_added'))} | source: {_show(row.get('date_added_source'))}",
        f"Action: {_string(row.get('action'))}",
    ]
    if kind in {"reply", "follow_up"}:
        refs = row.get("source_refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref for ref in refs):
            raise CombinedReportError("message_sources_invalid")
        message = row.get("message")
        if not isinstance(message, str):
            raise CombinedReportError("message_text_invalid")
        message_text = message if message else "[attachment-only message; no text; inspect cited sources]"
        lines.extend((f"Verified message at: {_string(row.get('message_at'))}", f"Message: {message_text}", f"Message sources: {', '.join(refs)}", f"Read state: {_show(row.get('read_state'))}"))
        if kind == "follow_up":
            lines.append(f"Elapsed hours: {_show(row.get('elapsed_hours'))}")
    else:
        links = _citations(row.get("citations"), key="source_url")
        if not links:
            raise CombinedReportError("first_dm_research_missing")
        lines.extend((f"Connected on: {_string(row.get('connected_on'))} (calendar day)", f"Connection source: {_string(row.get('connection_source'))}", f"Saved researched first-DM draft: {_string(row.get('draft'))}", f"Research sources: {', '.join(links) or 'unknown'}"))
    lines.append("")
    return lines


def combine_action_reports(invitations: Any, dm_actions: Any) -> dict[str, Any]:
    """Validate actual upstream envelopes and render one private owner action report."""
    inv = _object(invitations)
    dm = _object(dm_actions)
    _json_contract(inv)
    _json_contract(dm)
    if inv.get("schema_version") != 1 or inv.get("cap_scope") != "all_history" or inv.get("status") not in {"ready", "withheld_current_employer_assignments"}:
        raise CombinedReportError("invitation_contract_invalid")
    if "schema_version" in dm:
        raise CombinedReportError("dm_contract_invalid")
    inv_rows = _rows(inv.get("invitations"))
    research = _rows(inv.get("research_queue"))
    excluded_counts = _counts(inv.get("excluded_counts"))
    withheld_counts = _counts(inv.get("withheld_counts"))
    withheld = _rows(dm.get("withheld"))
    coverage = _object(dm.get("coverage"))
    reasons = coverage.get("reasons")
    if not isinstance(reasons, list) or not all(isinstance(reason, str) and reason for reason in reasons):
        raise CombinedReportError("dm_coverage_invalid")
    if len(inv_rows) > 25 or (inv.get("status") != "ready" and (inv_rows or not research)) or (inv.get("status") == "ready" and research):
        raise CombinedReportError("invitation_consistency_invalid")
    profiles = [_profile(row) for row in inv_rows]
    if len(profiles) != len(set(profiles)):
        raise CombinedReportError("invitation_identity_duplicate")
    queues = {key: _rows(dm.get(key)) for key in ("reply", "follow_up", "first_dm")}
    if reasons and any(queues.values()):
        raise CombinedReportError("dm_coverage_action_conflict")
    dm_profiles = [_profile(row) for rows in (*queues.values(), withheld) for row in rows]
    if len(dm_profiles) != len(set(dm_profiles)):
        raise CombinedReportError("dm_action_conflict")
    status = "partial" if inv.get("status") != "ready" or reasons or withheld or any(withheld_counts.values()) else "complete"
    lines = ["Private LinkedIn action report", f"Status: {status}", "Owner reviews and sends every invitation and DM manually.", ""]
    freshness = _object(inv.get("source_freshness"))
    lines.extend((f"Invitation source acquired: {_show(freshness.get('collected_at'))}", f"Upstream provider generation: {_show(freshness.get('upstream_provider_generation'))}", ""))
    lines.extend(("Prioritized invitations", ""))
    if inv.get("status") != "ready":
        lines.extend(("No invitation recommendations. Current employer assignments remain unresolved.", ""))
    elif not inv_rows:
        lines.extend(("No eligible invitations in the complete shortlist.", ""))
    for rank, row in enumerate(inv_rows, start=1):
        lines.extend(_invitation_lines(row, rank))
    lines.extend(("Invitation research queue", ""))
    for row in research:
        profile = "unknown" if "profile_url" in row and row["profile_url"] is None else _profile(row)
        lines.append(f"{profile} | evidence: {_show(row.get('evidence_id'))} | source: {_show(row.get('source_ref'))} | event day: {_show(row.get('event_date'))} | reason: {_string(row.get('reason'))}")
    lines.append("")
    lines.extend(("Invitation exclusions and withheld candidates", ""))
    for label, counts in (("Excluded", excluded_counts), ("Withheld", withheld_counts)):
        lines.append(f"{label}: {', '.join(f'{reason}: {count}' for reason, count in sorted(counts.items())) or 'none'}")
    lines.append("")
    usage = inv.get("company_usage")
    if usage is not None:
        lines.extend(("Lifetime company capacity", ""))
        for company, raw in _object(usage).items():
            item = _object(raw)
            lines.append(f"{company} | recorded: {_show(item.get('recorded'))} | remaining slots: {_show(item.get('remaining_slots'))}")
            for source in _rows(item.get("evidence")):
                lines.append(f"Source: {_show(source.get('profile_url'))} | evidence: {_show(source.get('evidence_id'))} | pointer: {_show(source.get('source_ref'))} | event day: {_show(source.get('event_date'))}")
        lines.append("")
    employers = inv.get("current_employer_assignments")
    if employers is not None:
        lines.extend(("Current employer assignments", ""))
        for row in _rows(employers):
            links = _citations(row.get("citations"), key="url")
            lines.append(f"{_profile(row)} | company: {_show(row.get('company_id'))} | sources: {', '.join(links) or 'unknown'}")
        lines.append("")
    for title, key in (("Replies to write", "reply"), ("Follow-ups to write", "follow_up"), ("Researched first-DM drafts to review", "first_dm")):
        lines.extend((title, ""))
        if reasons:
            lines.extend(("Actions withheld because DM source coverage is incomplete.", ""))
        elif not queues[key]:
            lines.extend(("No action in this queue.", ""))
        for row in queues[key]:
            lines.extend(_dm_lines(row, key))
    lines.extend(("DM withheld queue", ""))
    for row in withheld:
        lines.append(f"{_profile(row)} | reason: {_string(row.get('reason'))} | prospect: {_show(row.get('prospect_id'))} | date added: {_show(row.get('date_added'))} | source: {_show(row.get('date_added_source'))}")
    lines.extend(("", "DM coverage", f"Reasons: {', '.join(reasons) or 'none'}", f"Upstream freshness: {_show(coverage.get('upstream_freshness'))}", f"Changelog scope: {_show(coverage.get('changelog_scope'))}"))
    text = validate_report_text("\n".join(lines))
    return {"schema_version": 1, "status": status, "invitation_shortlist": dict(inv), "dm_actions": dict(dm), "text": text}
