"""Build a private, evidence-backed invitation shortlist without sending messages."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from linkedin_mdp_mcp.inbox_sync import normalize_profile_url


class ShortlistInputError(ValueError):
    """Identify a fixed, safe input-boundary error without including private values."""

    def __init__(self, code: str):
        """Expose only a stable error code suitable for private CLI diagnostics."""
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Citation:
    """Retain dated source references for one qualification or ranking fact."""

    url: str
    source_date: date | None
    retrieved_at: datetime


@dataclass(frozen=True)
class Company:
    """Map a verified company identity to its explicit employer aliases."""

    company_id: str
    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class Factor:
    """Represent one known ranking signal and the evidence that supports it."""

    value: bool | int
    observed_at: datetime
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class QualifiedCandidate:
    """Hold exact identity, current qualification facts, and optional ranking evidence."""

    profile_url: str
    name: str
    role: str
    company_id: str
    company_name: str
    citations: Mapping[str, tuple[Citation, ...]]
    factors: Mapping[str, Factor]


@dataclass(frozen=True)
class HistoryEvidence:
    """Identify one invitation event or connection exclusion with its source pointer."""

    evidence_id: str
    profile_url: str | None
    source_ref: str
    event_date: date | None
    source_time: str | None = None
    time_precision: str = "unknown"
    timezone: str = "unknown"


_INVITATION_EVENT_TYPES = {
    "LINKEDIN_INVITE_SENT",
    "LINKEDIN_INVITATION_HISTORY_FOUND",
}
_CONNECTION_EVENT_TYPES = {
    "LINKEDIN_CONNECTION_FOUND",
}
_SUPPRESSION_EVENT_TYPES = {
    "LINKEDIN_OPTED_OUT",
    "LINKEDIN_OPT_OUT",
    "LINKEDIN_DO_NOT_CONTACT",
    "PROSPECT_SUPPRESSED",
    "OUTREACH_OPT_OUT",
}
_SUPPRESSION_STATUS_VALUES = {
    "do_not_contact",
    "not_interested",
    "opt_out",
    "opted_out",
    "suppressed",
    "unsubscribed",
}
_INVITATION_SENT_STATUS_VALUES = {"accepted", "sent", "sent_unknown", "invite_sent"}
_CRM_SENT_STATUS_VALUES = {"sent", "sent_unknown", "invite_sent"}
_SCORE_WEIGHTS = {"activity": 4.0, "mutual_connections": 3.0, "connection_count": 2.0, "profile_photo": 1.0}
_SCORE_VERSION = "invitation-priority-v1"
_MAX_SOURCE_AGE = timedelta(hours=24)
_QUALIFICATION_MAX_AGE = timedelta(days=90)
_ACTIVITY_MAX_AGE = timedelta(days=30)
_PROFILE_MAX_AGE = timedelta(days=90)


def _object(value: Any, code: str) -> Mapping[str, Any]:
    """Require a mapping at an untrusted JSON boundary."""
    if not isinstance(value, Mapping):
        raise ShortlistInputError(code)
    return value


def _array(value: Any, code: str) -> Sequence[Any]:
    """Require a JSON array at an untrusted boundary."""
    if not isinstance(value, list):
        raise ShortlistInputError(code)
    return value


def _timestamp(value: Any, code: str) -> datetime:
    """Parse an explicit timezone-bearing ISO timestamp."""
    if not isinstance(value, str) or not value.strip():
        raise ShortlistInputError(code)
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ShortlistInputError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ShortlistInputError(code)
    return parsed.astimezone(UTC)


def _date(value: Any, code: str) -> date:
    """Parse an explicit ISO calendar date at an untrusted boundary."""
    if not isinstance(value, str):
        raise ShortlistInputError(code)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ShortlistInputError(code) from exc
    if parsed.isoformat() != value:
        raise ShortlistInputError(code)
    return parsed


def _citation_list(value: Any, now: datetime, *, max_age: timedelta) -> tuple[Citation, ...]:
    """Validate dated HTTP citations and reject stale or future-dated evidence."""
    items = _array(value, "qualification_missing_citation")
    citations: list[Citation] = []
    for item in items:
        row = _object(item, "qualification_invalid_citation")
        url = row.get("url")
        if not isinstance(url, str):
            raise ShortlistInputError("qualification_invalid_citation")
        try:
            parsed_url = urlsplit(url)
        except ValueError as exc:
            raise ShortlistInputError("qualification_invalid_citation") from exc
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname or parsed_url.username or parsed_url.password:
            raise ShortlistInputError("qualification_invalid_citation")
        source_date_value = row.get("source_date")
        source_date: date | None = None
        if source_date_value is not None:
            if not isinstance(source_date_value, str):
                raise ShortlistInputError("qualification_invalid_citation")
            try:
                source_date = date.fromisoformat(source_date_value)
            except ValueError as exc:
                raise ShortlistInputError("qualification_invalid_citation") from exc
        retrieved_at = _timestamp(row.get("retrieved_at"), "qualification_invalid_citation")
        if source_date is not None and (source_date > now.date() or (now.date() - source_date).days > max_age.days):
            raise ShortlistInputError("qualification_stale")
        if retrieved_at > now + timedelta(minutes=5):
            raise ShortlistInputError("qualification_invalid_citation")
        if now - retrieved_at > max_age:
            raise ShortlistInputError("qualification_stale")
        citations.append(Citation(url, source_date, retrieved_at))
    if not citations:
        raise ShortlistInputError("qualification_missing_citation")
    return tuple(citations)


def _company_registry(value: Any) -> tuple[dict[str, Company], dict[str, str]]:
    """Validate company IDs and build a collision-free, exact normalized alias map."""
    companies: dict[str, Company] = {}
    aliases: dict[str, str] = {}
    for item in _array(value, "qualification_invalid_company_registry"):
        row = _object(item, "qualification_invalid_company_registry")
        company_id = row.get("company_id")
        name = row.get("name")
        alias_values = row.get("aliases")
        if not isinstance(company_id, str) or not company_id.strip() or not isinstance(name, str) or not name.strip():
            raise ShortlistInputError("qualification_invalid_company_registry")
        existing_company = companies.get(company_id)
        if existing_company is not None and existing_company.name != name.strip():
            raise ShortlistInputError("company_id_conflict")
        alias_rows = _array(alias_values, "qualification_invalid_company_registry")
        normalized_aliases: list[str] = []
        for alias in [name, *alias_rows]:
            if not isinstance(alias, str) or not alias.strip():
                raise ShortlistInputError("qualification_invalid_company_registry")
            key = " ".join(alias.casefold().split())
            existing = aliases.get(key)
            if existing is not None and existing != company_id:
                raise ShortlistInputError("company_alias_conflict")
            aliases[key] = company_id
            if key not in normalized_aliases:
                normalized_aliases.append(key)
        companies[company_id] = Company(company_id, name.strip(), tuple(normalized_aliases))
    return companies, aliases


def _parse_factor(name: str, value: Any, now: datetime) -> Factor | None:
    """Validate one optional dated ranking factor, preserving absent or stale values as unknown."""
    if value is None:
        return None
    row = _object(value, "qualification_invalid_rank_factor")
    observed_at = _timestamp(row.get("observed_at"), "qualification_invalid_rank_factor")
    age_limit = _ACTIVITY_MAX_AGE if name == "activity" else _PROFILE_MAX_AGE
    if observed_at > now or now - observed_at > age_limit:
        return None
    raw_value = row.get("value")
    if name in {"activity", "profile_photo"}:
        if not isinstance(raw_value, bool):
            raise ShortlistInputError("qualification_invalid_rank_factor")
    elif not isinstance(raw_value, int) or isinstance(raw_value, bool) or raw_value < 0:
        raise ShortlistInputError("qualification_invalid_rank_factor")
    citations = _citation_list(row.get("citations"), now, max_age=age_limit)
    return Factor(raw_value, observed_at, citations)


def _candidate(row: Mapping[str, Any], companies: Mapping[str, Company], aliases: Mapping[str, str], now: datetime) -> tuple[QualifiedCandidate | None, str | None]:
    """Parse one candidate and return a stable withholding reason for qualification gaps."""
    profile_url = normalize_profile_url(row.get("profile_url"))
    if profile_url is None:
        return None, "identity_unresolved"
    identity = row.get("identity")
    if not isinstance(identity, Mapping):
        return None, "identity_unresolved"
    name_obj = identity.get("name")
    role_obj = identity.get("current_role")
    country_obj = identity.get("country")
    employer = identity.get("pvf_employer")
    required = (("name", name_obj, "qualification_missing_name"), ("current_role", role_obj, "qualification_missing_role"), ("country", country_obj, "qualification_missing_country"), ("pvf_employer", employer, "qualification_missing_pvf_employer"))
    parsed_citations: dict[str, tuple[Citation, ...]] = {}
    values: dict[str, Any] = {}
    for key, item, reason in required:
        if not isinstance(item, Mapping):
            return None, reason
        try:
            parsed_citations[key] = _citation_list(item.get("citations"), now, max_age=_QUALIFICATION_MAX_AGE)
        except ShortlistInputError as exc:
            return None, exc.code
        if key != "pvf_employer":
            value = item.get("value")
            if not isinstance(value, str) or not value.strip():
                return None, reason
            values[key] = value.strip()
    if values.get("country", "").casefold() not in {"us", "united states", "united states of america"}:
        return None, "qualification_not_us"
    company_id = employer.get("company_id") if isinstance(employer, Mapping) else None
    company_name = employer.get("company_name") if isinstance(employer, Mapping) else None
    if not isinstance(employer, Mapping) or employer.get("value") is not True:
        return None, "qualification_missing_pvf_employer"
    if not isinstance(company_id, str) or company_id not in companies or not isinstance(company_name, str) or not company_name.strip():
        return None, "qualification_company_unverified"
    alias_company_id = aliases.get(" ".join(company_name.casefold().split()))
    if alias_company_id != company_id:
        return None, "qualification_company_conflict"
    factors_value = row.get("rank_factors", {})
    if not isinstance(factors_value, Mapping):
        return None, "qualification_invalid_rank_factors"
    factors: dict[str, Factor] = {}
    for factor_name in _SCORE_WEIGHTS:
        if factor_name not in factors_value:
            continue
        try:
            factor = _parse_factor(factor_name, factors_value[factor_name], now)
        except ShortlistInputError as exc:
            return None, exc.code
        if factor is not None:
            factors[factor_name] = factor
    return QualifiedCandidate(profile_url, values["name"], values["current_role"], company_id, companies[company_id].name, parsed_citations, factors), None


def _validate_provider_snapshot(value: Any, domain: str) -> list[Mapping[str, Any]]:
    """Require successful complete collector coverage and exported rows found in raw pages."""
    snapshot = _object(value, "source_snapshot_missing")
    if snapshot.get("source_result") != "success" or snapshot.get("truncated") is not False:
        raise ShortlistInputError("source_snapshot_incomplete")
    page_count = snapshot.get("page_count")
    raw_elements = _array(snapshot.get("raw_elements"), "source_snapshot_incomplete")
    rows = _array(snapshot.get("rows"), "source_snapshot_incomplete")
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1 or not raw_elements:
        raise ShortlistInputError("source_snapshot_incomplete")
    raw_rows: list[Any] = []
    for element in raw_elements:
        page = _object(element, "source_snapshot_incomplete")
        if page.get("snapshotDomain") != domain:
            raise ShortlistInputError("source_snapshot_domain_mismatch")
        raw_rows.extend(_array(page.get("snapshotData"), "source_snapshot_incomplete"))
    if any(not isinstance(row, Mapping) for row in raw_rows):
        raise ShortlistInputError("source_row_invalid")
    valid_rows: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ShortlistInputError("source_row_invalid")
        valid_rows.append(row)
    raw_row_keys = {_stable_hash(row) for row in raw_rows}
    exported_row_keys = {_stable_hash(row) for row in valid_rows}
    if len(exported_row_keys) != len(valid_rows) or raw_row_keys != exported_row_keys:
        raise ShortlistInputError("source_rows_do_not_match_raw_pages")
    return valid_rows


def _validate_database_snapshot(value: Any, name: str) -> list[Mapping[str, Any]]:
    """Require complete paged Supabase rows with the collector's stable-count contract."""
    snapshot = _object(value, "source_database_snapshot_missing")
    rows = _array(snapshot.get("rows"), "source_database_snapshot_incomplete")
    count = snapshot.get("row_count")
    pages = snapshot.get("page_count")
    if (
        snapshot.get("source_result") != "success"
        or snapshot.get("truncated") is not False
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count != len(rows)
        or not isinstance(pages, int)
        or isinstance(pages, bool)
        or pages < 1
        or not isinstance(snapshot.get("consistency"), str)
        or not snapshot["consistency"].strip()
    ):
        raise ShortlistInputError(f"source_{name}_snapshot_incomplete")
    valid_rows: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ShortlistInputError(f"source_{name}_row_invalid")
        valid_rows.append(row)
    if name == "prospects":
        prospect_ids: set[str] = set()
        for row in valid_rows:
            prospect_id = row.get("id")
            if not isinstance(prospect_id, str) or not prospect_id.strip():
                raise ShortlistInputError("source_prospects_invalid_id")
            if prospect_id in prospect_ids:
                raise ShortlistInputError("source_prospects_duplicate_id")
            prospect_ids.add(prospect_id)
    return valid_rows


def _source_profile_index(prospects: Sequence[Mapping[str, Any]]) -> tuple[dict[str, set[str]], set[str]]:
    """Index exact canonical profile URLs and retain conflicting prospect IDs."""
    ids_by_url: dict[str, set[str]] = defaultdict(set)
    for row in prospects:
        profile_url = normalize_profile_url(row.get("linkedin_url"))
        prospect_id = row.get("id")
        if profile_url is None or not isinstance(prospect_id, str) or not prospect_id:
            continue
        ids_by_url[profile_url].add(prospect_id)
    conflicts = {profile_url for profile_url, prospect_ids in ids_by_url.items() if len(prospect_ids) > 1}
    return ids_by_url, conflicts


def _stable_hash(value: Any) -> str:
    """Return a deterministic opaque identifier for a JSON value."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _history_evidence_id(kind: str, value: Any) -> str:
    """Build a safe evidence key without copying source names, URLs, or message text."""
    return f"history-evidence-sha256:{_stable_hash([kind, value])}"


def _profile_queue_id(profile_url: str) -> str:
    """Build a safe queue identifier for an exact profile missing history evidence."""
    return f"profile-sha256:{_stable_hash(profile_url)}"


def _optional_timestamp(value: Any) -> datetime | None:
    """Read a source timestamp when it is valid without inventing a missing date."""
    try:
        return _timestamp(value, "source_event_time_invalid")
    except ShortlistInputError:
        return None


def _provider_local_date(value: Any) -> date | None:
    """Read the collector's M/D/YY local timestamp as a date without inventing its zone."""
    if not isinstance(value, str):
        return None
    try:
        parsed = time.strptime(value, "%m/%d/%y, %I:%M %p")
        return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
    except ValueError:
        return None


def _event_history(events: Sequence[Mapping[str, Any]], prospect_urls: Mapping[str, str]) -> list[HistoryEvidence]:
    """Extract sent-invitation evidence from Supabase for lifetime cap accounting."""
    result: list[HistoryEvidence] = []
    for index, row in enumerate(events):
        event_type = row.get("event_type")
        if event_type not in _INVITATION_EVENT_TYPES:
            continue
        prospect_id = row.get("prospect_id")
        profile_url = prospect_urls.get(prospect_id) if isinstance(prospect_id, str) else None
        event_key = row.get("external_key") or row.get("id")
        identity = [row.get("source"), event_key if event_key else [event_type, prospect_id, index]]
        evidence_id = _history_evidence_id("event", identity)
        payload_value = row.get("payload")
        payload = payload_value if isinstance(payload_value, Mapping) else {}
        semantics = payload.get("timestamp_semantics")
        precision = payload.get("timestamp_precision")
        source_time = row.get("occurred_at")
        event_at = _optional_timestamp(source_time)
        event_date: date | None = None
        time_precision = "unknown"
        timezone = "unknown"
        if semantics == "provider_local_time_timezone_unspecified" and isinstance(payload.get("provider_sent_at"), str):
            source_time = payload["provider_sent_at"]
            event_date = _provider_local_date(source_time)
            time_precision = "provider_local_day" if event_date else "unknown"
        elif semantics == "actual" and precision == "date" and event_at is not None:
            event_date = event_at.date()
            time_precision = "calendar_day"
        elif semantics == "actual" and precision == "instant" and event_at is not None:
            event_date = event_at.date()
            time_precision = "instant"
            timezone = "explicit"
        result.append(HistoryEvidence(
            evidence_id,
            profile_url,
            f"events.rows[{index}]",
            event_date,
            source_time if isinstance(source_time, str) else None,
            time_precision,
            timezone,
        ))
    return result


def _crm_history(prospects: Sequence[Mapping[str, Any]]) -> list[HistoryEvidence]:
    """Retain explicit CRM sent facts as event-level history needing employer proof."""
    result: list[HistoryEvidence] = []
    for index, row in enumerate(prospects):
        attributes = _attributes(row)
        if not _crm_sent(attributes):
            continue
        profile_url = normalize_profile_url(row.get("linkedin_url"))
        sent_date = attributes.get("Invite Sent Date")
        event_date: date | None = None
        if isinstance(sent_date, str):
            try:
                event_date = _date(sent_date, "crm_invitation_date_invalid")
            except ShortlistInputError:
                pass
        evidence_id = _history_evidence_id("crm-invitation", [row.get("id"), profile_url, sent_date])
        result.append(HistoryEvidence(
            evidence_id,
            profile_url,
            f"prospects.rows[{index}]",
            event_date,
            sent_date if isinstance(sent_date, str) else None,
            "calendar_day" if event_date else "unknown",
            "unknown",
        ))
    return result


def _provider_history(invitations: Sequence[Mapping[str, Any]], connections: Sequence[Mapping[str, Any]]) -> tuple[list[HistoryEvidence], set[str], list[HistoryEvidence]]:
    """Collect outgoing invitations for the company cap and connections for exclusion."""
    result: list[HistoryEvidence] = []
    connected: set[str] = set()
    unresolved: list[HistoryEvidence] = []
    for index, row in enumerate(invitations):
        direction = row.get("Direction")
        if not isinstance(direction, str):
            raise ShortlistInputError("invitation_direction_missing")
        direction_key = "_".join(direction.strip().casefold().replace("-", " ").split())
        if direction_key in {"incoming", "inbound"}:
            continue
        if direction_key not in {"outgoing", "outbound", "sent"}:
            raise ShortlistInputError("invitation_direction_unknown")
        raw_url = row.get("inviteeProfileUrl")
        profile_url = normalize_profile_url(raw_url)
        evidence_id = _history_evidence_id("invitation", row)
        source_time = row.get("Sent At")
        event_date = _provider_local_date(source_time)
        result.append(HistoryEvidence(
            evidence_id,
            profile_url,
            f"snapshots.INVITATIONS.rows[{index}]",
            event_date,
            source_time if isinstance(source_time, str) else None,
            "provider_local_day" if event_date else "unknown",
            "unknown",
        ))
        if profile_url is None:
            unresolved.append(result[-1])
    for index, row in enumerate(connections):
        profile_url = normalize_profile_url(row.get("URL"))
        if profile_url is None:
            evidence_id = _history_evidence_id("connection", row)
            unresolved.append(HistoryEvidence(evidence_id, None, f"snapshots.CONNECTIONS.rows[{index}]", _provider_local_date(row.get("Connected On"))))
            continue
        connected.add(profile_url)
    return result, connected, unresolved


def _event_suppressed(events: Sequence[Mapping[str, Any]], prospect_ids: set[str]) -> bool:
    """Check exact prospect-linked suppression events."""
    return any(row.get("prospect_id") in prospect_ids and row.get("event_type") in _SUPPRESSION_EVENT_TYPES for row in events)


def _attributes(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read CRM attributes only when their stored shape is an object."""
    value = row.get("attributes")
    return value if isinstance(value, Mapping) else {}


def _key(value: str) -> str:
    """Normalize known CRM field and status labels without fuzzy identity matching."""
    return "_".join("".join(char.lower() if char.isalnum() else " " for char in value).split())


def _suppressed(attributes: Mapping[str, Any]) -> bool:
    """Recognize explicit suppression fields and a fixed set of terminal status values."""
    for raw_key, value in attributes.items():
        if not isinstance(raw_key, str):
            continue
        key = _key(raw_key)
        if key in {"opt_out", "opted_out", "do_not_contact", "suppressed", "linkedin_opt_out"} and value is True:
            return True
        if (
            key in {"status", "linkedin_status", "invite_status", "next_action"}
            and isinstance(value, str)
            and _key(value) in _SUPPRESSION_STATUS_VALUES
        ):
                return True
    return False


def _crm_invited(attributes: Mapping[str, Any]) -> bool:
    """Recognize only explicit invitation or acceptance facts in CRM fields."""
    for raw_key, value in attributes.items():
        if not isinstance(raw_key, str):
            continue
        key = _key(raw_key)
        if key in {"invite_sent_date", "accepted_date"} and isinstance(value, str) and value.strip():
            return True
        if (
            key in {"invite_status", "linkedin_status"}
            and isinstance(value, str)
            and _key(value) in _INVITATION_SENT_STATUS_VALUES
        ):
                return True
    return False


def _crm_sent(attributes: Mapping[str, Any]) -> bool:
    """Recognize outgoing CRM invitation evidence without treating acceptance as a send."""
    for raw_key, value in attributes.items():
        if not isinstance(raw_key, str):
            continue
        key = _key(raw_key)
        if key == "invite_sent_date" and isinstance(value, str) and value.strip():
            return True
        if key in {"invite_status", "linkedin_status"} and isinstance(value, str) and _key(value) in _CRM_SENT_STATUS_VALUES:
            return True
    return False


def _crm_positive(attributes: Mapping[str, Any]) -> bool:
    """Recognize explicit invitations and current connections for candidate exclusion."""
    if _crm_invited(attributes):
        return True
    return any(
        isinstance(key, str)
        and _key(key) == "connection_status"
        and isinstance(value, str)
        and _key(value) in {"accepted", "connected"}
        for key, value in attributes.items()
    )


def _factor_score(factor: Factor, name: str) -> float:
    """Score a known signal with fixed heuristic weights and bounded numeric inputs."""
    weight = _SCORE_WEIGHTS[name]
    if name == "activity":
        return weight if factor.value is True else 0.0
    if name == "profile_photo":
        return weight if factor.value is True else 0.0
    if name == "mutual_connections":
        return weight * min(int(factor.value), 20) / 20
    return weight * min(int(factor.value), 500) / 500


def _citation_json(citation: Citation) -> dict[str, Any]:
    """Serialize one dated citation for private report output."""
    return {
        "url": citation.url,
        "source_date": citation.source_date.isoformat() if citation.source_date else None,
        "retrieved_at": citation.retrieved_at.isoformat(),
    }


def _candidate_json(candidate: QualifiedCandidate) -> dict[str, Any]:
    """Serialize a ranked row with explicit evidence, unknowns, and no acceptance estimate."""
    score_factors: dict[str, Any] = {}
    unknown: dict[str, str] = {}
    score = 0.0
    for name in _SCORE_WEIGHTS:
        factor = candidate.factors.get(name)
        if factor is None:
            unknown[name] = "not_observed_or_stale"
            continue
        score += _factor_score(factor, name)
        score_factors[name] = {
            "value": factor.value,
            "observed_at": factor.observed_at.isoformat(),
            "citations": [_citation_json(item) for item in factor.citations],
            "points": round(_factor_score(factor, name), 3),
        }
    return {
        "profile_url": candidate.profile_url,
        "name": candidate.name,
        "role": candidate.role,
        "company_id": candidate.company_id,
        "company_name": candidate.company_name,
        "score": round(score, 3) if score_factors else None,
        "score_version": _SCORE_VERSION,
        "score_factors": score_factors,
        "unknown_factors": unknown,
        "evidence": {name: [_citation_json(item) for item in citations] for name, citations in candidate.citations.items()},
        "reason": "US PVF fit; priority uses dated, exact-profile evidence",
        "action": "Review and send a LinkedIn invitation manually",
    }


def _make_markdown(result: Mapping[str, Any]) -> str:
    """Render a private person-level invitation report from the structured result."""
    lines = ["# LinkedIn invitation shortlist", "", f"Status: {result['status']}", "", f"Cap scope: {result['cap_scope']}", "", "Upstream provider freshness: unknown unless retained provider metadata establishes it.", ""]
    if result["status"] != "ready":
        lines.extend(["No invitation recommendations were produced.", ""])
    if result["invitations"]:
        lines.extend(["## Prioritized invitations", ""])
        for index, item in enumerate(result["invitations"], start=1):
            lines.extend([
                f"### {index}. [{item['name']}]({item['profile_url']})",
                "",
                f"{item['role']} at {item['company_name']}. Priority score {item['score'] if item['score'] is not None else 'unknown'} ({item['score_version']}); this is not an acceptance probability.",
                "",
                f"Reason: {item['reason']}.",
                "",
                f"Action: {item['action']}.",
                "",
                "Factors: " + ", ".join(f"{name}={json.dumps(value['value'])}" for name, value in item["score_factors"].items()) + ("; unknown: " + ", ".join(item["unknown_factors"]) if item["unknown_factors"] else ""),
                "",
                "Evidence: " + ", ".join(citation["url"] for citations in item["evidence"].values() for citation in citations),
                "",
            ])
    if result["research_queue"]:
        lines.extend(["## Historical company evidence to resolve", "", "These opaque records must be researched before an all-history cap can be claimed.", ""])
        for row in result["research_queue"]:
            pointer = ", ".join(
                value for value in (
                    row.get("profile_url"),
                    row.get("evidence_id"),
                    row.get("source_ref"),
                    row.get("event_date"),
                ) if value is not None
            )
            lines.append(f"- {pointer}: {row['reason']}.")
        lines.append("")
    lines.extend(["## Audit", "", f"Score version: `{_SCORE_VERSION}`. Recommendations are event plans only. No invitation was sent.", ""])
    return "\n".join(lines)


def _source_history(invitations: Sequence[Mapping[str, Any]], connections: Sequence[Mapping[str, Any]], prospects: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]) -> tuple[list[HistoryEvidence], set[str], set[str], list[HistoryEvidence]]:
    """Combine provider, CRM, and event positives into exact exclusions and cap evidence."""
    profile_by_id = {
        str(row["id"]): profile_url
        for row in prospects
        if isinstance(row.get("id"), str)
        and (profile_url := normalize_profile_url(row.get("linkedin_url"))) is not None
    }
    provider_events, connected_profiles, unresolved_connections = _provider_history(invitations, connections)
    event_history = _event_history(events, profile_by_id)
    crm_history = _crm_history(prospects)
    historical_profiles = {item.profile_url for item in [*provider_events, *event_history, *crm_history] if item.profile_url}
    for row in events:
        if row.get("event_type") not in _CONNECTION_EVENT_TYPES:
            continue
        prospect_id = row.get("prospect_id")
        profile_url = profile_by_id.get(prospect_id) if isinstance(prospect_id, str) else None
        if profile_url:
            connected_profiles.add(profile_url)
    sent_profiles: set[str] = set()
    for row in prospects:
        profile_url = normalize_profile_url(row.get("linkedin_url"))
        prospect_id = row.get("id")
        if not profile_url or not isinstance(prospect_id, str):
            continue
        if _crm_positive(_attributes(row)):
            sent_profiles.add(profile_url)
    for row in events:
        if row.get("event_type") in _INVITATION_EVENT_TYPES:
            prospect_id = row.get("prospect_id")
            profile_url = profile_by_id.get(prospect_id) if isinstance(prospect_id, str) else None
            if profile_url:
                sent_profiles.add(profile_url)
    return [*provider_events, *event_history, *crm_history], connected_profiles, historical_profiles | sent_profiles, unresolved_connections


def build_invitation_shortlist(
    source_data: Mapping[str, Any],
    qualification_data: Mapping[str, Any],
    *,
    cap_scope: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a deterministic shortlist only from complete, recent, exact-identity evidence."""
    if cap_scope != "all_history":
        raise ShortlistInputError("cap_scope_invalid")
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ShortlistInputError("clock_timezone_invalid")
    now = now.astimezone(UTC)
    source = _object(source_data, "source_invalid")
    qualification = _object(qualification_data, "qualification_invalid")
    if source.get("schema_version") != 1 or qualification.get("schema_version") != 1:
        raise ShortlistInputError("schema_version_unsupported")
    collected_at = _timestamp(source.get("collected_at"), "source_collection_time_invalid")
    source_age = now - collected_at
    if source_age < timedelta(0) or source_age > _MAX_SOURCE_AGE:
        raise ShortlistInputError("source_stale")
    snapshots = _object(source.get("snapshots"), "source_snapshots_missing")
    provider_rows = {
        domain: _validate_provider_snapshot(snapshots.get(domain), domain)
        for domain in ("CONNECTIONS", "INVITATIONS", "INBOX")
    }
    prospects = _validate_database_snapshot(source.get("prospects"), "prospects")
    events = _validate_database_snapshot(source.get("events"), "events")
    registry, aliases = _company_registry(qualification.get("company_registry"))
    prospect_ids_by_url, conflicting_profiles = _source_profile_index(prospects)
    history, connected_profiles, prior_profiles, unresolved_connections = _source_history(provider_rows["INVITATIONS"], provider_rows["CONNECTIONS"], prospects, events)
    history_by_id: dict[str, HistoryEvidence] = {}
    for item in history:
        if item.evidence_id in history_by_id:
            raise ShortlistInputError("source_history_identity_duplicate")
        history_by_id[item.evidence_id] = item
    candidates_data = _array(qualification.get("candidates"), "qualification_candidates_missing")
    parsed_candidates: list[QualifiedCandidate] = []
    withheld_counts: Counter[str] = Counter()
    seen_candidates: set[str] = set()
    duplicate_candidates: set[str] = set()
    for item in candidates_data:
        if not isinstance(item, Mapping):
            withheld_counts["identity_unresolved"] += 1
            continue
        candidate_row, reason = _candidate(item, registry, aliases, now)
        if candidate_row is None:
            withheld_counts[reason or "qualification_invalid"] += 1
            continue
        if candidate_row.profile_url in seen_candidates:
            duplicate_candidates.add(candidate_row.profile_url)
        seen_candidates.add(candidate_row.profile_url)
        parsed_candidates.append(candidate_row)
    if duplicate_candidates:
        withheld_counts["duplicate_candidate_identity"] += len(duplicate_candidates)

    excluded_counts: Counter[str] = Counter()
    eligible: list[QualifiedCandidate] = []
    for candidate_row in parsed_candidates:
        url = candidate_row.profile_url
        if url in duplicate_candidates or url in conflicting_profiles:
            withheld_counts["identity_conflict"] += 1
            continue
        prospect_ids = {
            str(row["id"])
            for row in prospects
            if row.get("id") and normalize_profile_url(row.get("linkedin_url")) == url
        }
        attrs_rows = [_attributes(row) for row in prospects if normalize_profile_url(row.get("linkedin_url")) == url]
        if any(_suppressed(attrs) for attrs in attrs_rows) or _event_suppressed(events, prospect_ids):
            excluded_counts["suppressed"] += 1
        elif url in connected_profiles:
            excluded_counts["connected"] += 1
        elif url in prior_profiles:
            excluded_counts["previously_invited"] += 1
        elif not prospect_ids_by_url.get(url):
            withheld_counts["prospect_not_found"] += 1
        else:
            eligible.append(candidate_row)

    company_counts: Counter[str] = Counter()
    cap_evidence_by_company: dict[str, list[str]] = defaultdict(list)
    history_queue: list[dict[str, Any]] = []
    research_queue: list[dict[str, Any]] = []
    for evidence in unresolved_connections:
        history_queue.append({
            "opaque_recipient_id": evidence.evidence_id,
            "evidence_id": evidence.evidence_id,
            "profile_url": None,
            "source_ref": evidence.source_ref,
            "event_date": evidence.event_date.isoformat() if evidence.event_date else None,
            "source_time": evidence.source_time,
            "time_precision": evidence.time_precision,
            "timezone": evidence.timezone,
            "reason": "unsupported_connected_profile_url" if evidence.source_ref.startswith("snapshots.CONNECTIONS") else "unsupported_historical_profile_url",
        })
    raw_bindings = _array(qualification.get("history_company_bindings"), "history_bindings_missing")
    bindings_by_id: dict[str, Mapping[str, Any]] = {}
    binding_errors: list[tuple[HistoryEvidence, str]] = []
    for binding_value in raw_bindings:
        if not isinstance(binding_value, Mapping):
            raise ShortlistInputError("company_at_invitation_binding_invalid")
            continue
        profile_url = normalize_profile_url(binding_value.get("profile_url"))
        opaque_id = binding_value.get("opaque_recipient_id")
        evidence_id = binding_value.get("evidence_id")
        company_id = binding_value.get("company_id")
        if not isinstance(evidence_id, str) or evidence_id not in history_by_id:
            raise ShortlistInputError("history_bindings_unmatched")
        history_item = history_by_id[evidence_id]
        if (history_item.profile_url is None and (profile_url is not None or opaque_id != evidence_id)) or (
            history_item.profile_url is not None and (profile_url != history_item.profile_url or opaque_id is not None)
        ):
            binding_errors.append((history_item, "unsupported_historical_profile_url"))
            continue
        if not isinstance(company_id, str) or company_id not in registry:
            binding_errors.append((history_item, "company_at_invitation_unverified"))
            continue
        try:
            binding_citations = _citation_list(binding_value.get("citations"), now, max_age=timedelta(days=36500))
            attested_event_date = _date(binding_value.get("event_date"), "history_binding_event_date_invalid")
            claim_date = _date(binding_value.get("historical_claim_date"), "history_binding_claim_date_invalid") if binding_value.get("historical_claim_date") is not None else None
            interval = _object(binding_value.get("employment_interval"), "history_binding_interval_invalid") if binding_value.get("employment_interval") is not None else None
            interval_start = _date(interval.get("start_date"), "history_binding_interval_invalid") if interval else None
            interval_end = _date(interval.get("end_date"), "history_binding_interval_invalid") if interval and interval.get("end_date") is not None else None
        except ShortlistInputError as exc:
            binding_errors.append((history_item, exc.code))
            continue
        event_date = history_item.event_date
        interval_covers = bool(interval_start and event_date and interval_start <= event_date and (interval_end is None or event_date <= interval_end))
        if (binding_value.get("company_at_event") is not True or not event_date or attested_event_date != event_date or
                not (interval_covers or claim_date == event_date) or not binding_citations):
            binding_errors.append((history_item, "company_at_invitation_unverified" if event_date else "invitation_timestamp_unknown"))
            continue
        if evidence_id in bindings_by_id:
            binding_errors.append((history_item, "duplicate_history_binding"))
            continue
        bindings_by_id[evidence_id] = binding_value
    missing_ids = set(history_by_id) - set(bindings_by_id)
    for evidence_id in sorted(missing_ids):
        evidence = history_by_id[evidence_id]
        reason = "invitation_timestamp_unknown" if evidence.event_date is None else ("company_at_invitation_unbound" if evidence.profile_url else "unsupported_historical_profile_url")
        binding_errors.append((evidence, reason))
    extra_ids = set(bindings_by_id) - set(history_by_id)
    if extra_ids:
        raise ShortlistInputError("history_bindings_unmatched")
    for evidence in history_by_id.values():
        if evidence.profile_url is None:
            binding_errors.append((evidence, "unsupported_historical_profile_url"))
    if binding_errors:
        for evidence, reason in binding_errors:
            history_queue.append({
                "opaque_recipient_id": evidence.evidence_id if evidence.profile_url is None else _profile_queue_id(evidence.profile_url),
                "evidence_id": evidence.evidence_id,
                "profile_url": evidence.profile_url,
                "source_ref": evidence.source_ref,
                "event_date": evidence.event_date.isoformat() if evidence.event_date else None,
                "source_time": evidence.source_time,
                "time_precision": evidence.time_precision,
                "timezone": evidence.timezone,
                "company_at_event_attestation_required": True,
                "reason": reason,
            })
    else:
        deduplicated: Counter[str] = Counter()
        profiles_by_company: dict[str, set[str]] = defaultdict(set)
        pairs = {
            (str(binding["company_id"]), history_by_id[evidence_id].profile_url)
            for evidence_id, binding in bindings_by_id.items()
        }
        for company_id, _profile_url in pairs:
            if _profile_url:
                deduplicated[company_id] += 1
                profiles_by_company[company_id].add(_profile_url)
        for evidence_id, binding in bindings_by_id.items():
            company_id = str(binding["company_id"])
            evidence = history_by_id[evidence_id]
            if evidence.profile_url in profiles_by_company[company_id]:
                cap_evidence_by_company[company_id].append(evidence_id)
        company_counts = deduplicated
    research_queue.extend(history_queue)
    unique_queue: list[dict[str, Any]] = []
    seen_queue: set[tuple[str, str]] = set()
    for queue_item in research_queue:
        key = (queue_item["evidence_id"], queue_item["reason"])
        if key not in seen_queue:
            unique_queue.append(queue_item)
            seen_queue.add(key)
    research_queue = unique_queue

    if research_queue:
        status = "withheld_history_company_bindings"
        selected: list[QualifiedCandidate] = []
    else:
        status = "ready"
        slots = {company_id: max(0, 3 - company_counts[company_id]) for company_id in registry}
        eligible.sort(key=lambda item: (-(sum(_factor_score(item.factors[name], name) for name in item.factors) if item.factors else -math.inf), item.profile_url))
        selected = []
        for candidate_row in eligible:
            if slots[candidate_row.company_id] <= 0:
                continue
            selected.append(candidate_row)
            slots[candidate_row.company_id] -= 1
            if len(selected) == 25:
                break
        remaining_slots = {company_id: max(0, 3 - company_counts[company_id]) for company_id in registry}
        for candidate_row in selected:
            remaining_slots[candidate_row.company_id] -= 1
        slots = remaining_slots
    research_queue.sort(key=lambda item: (item["evidence_id"], item["reason"]))

    source_digest = _stable_hash(source)
    rows_out = [_candidate_json(item) for item in selected]
    event_plan = [
        {
            "prospect_id": next((str(row["id"]) for row in prospects if normalize_profile_url(row.get("linkedin_url")) == item.profile_url), None),
            "source": "AGENT_ACTION_REPORT",
            "event_type": "LINKEDIN_INVITATION_RECOMMENDED",
            "external_key": f"invitation-recommended:{_stable_hash([item.profile_url, _SCORE_VERSION, collected_at.isoformat()])}",
            "occurred_at": now.isoformat(),
            "payload": {
                "recommendation_only": True,
                "profile_url": item.profile_url,
                "score_version": _SCORE_VERSION,
                "score": _candidate_json(item)["score"],
                "score_factors": _candidate_json(item)["score_factors"],
                "unknown_factors": _candidate_json(item)["unknown_factors"],
                "qualification_evidence": _candidate_json(item)["evidence"],
                "qualification_attestation": "input facts are explicitly attested; citation URLs are retained but page contents are not fetched by this builder",
                "cap_evidence": {
                    "scope": cap_scope,
                    "company_id": item.company_id,
                    "recorded_count": company_counts[item.company_id],
                    "source_digest": source_digest,
                    "history_evidence_ids": sorted(cap_evidence_by_company[item.company_id]),
                },
            },
        }
        for item in selected
    ]
    company_usage: dict[str, dict[str, int | None]] = (
        {company_id: {"recorded": None, "remaining_slots": None} for company_id in sorted(registry)}
        if status != "ready"
        else {
            company_id: {
                "recorded": company_counts[company_id],
                "remaining_slots": max(0, 3 - company_counts[company_id]),
            }
            for company_id in sorted(registry)
        }
    )
    output: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "cap_scope": cap_scope,
        "score_version": _SCORE_VERSION,
        "evidence_validation": "input facts and company bindings are explicit attestations; citation structure and dates are validated, but external page contents are not fetched",
        "source_freshness": {
            "acquisition": "fresh",
            "collected_at": collected_at.isoformat(),
            "age_seconds": int(source_age.total_seconds()),
            "upstream_provider_generation": "unknown" if any(snapshots[name].get("provider_generated_at") is None for name in ("CONNECTIONS", "INVITATIONS", "INBOX")) else "provided",
        },
        "source_counts": {
            "connections": len(provider_rows["CONNECTIONS"]),
            "invitations": len(provider_rows["INVITATIONS"]),
            "inbox": len(provider_rows["INBOX"]),
            "prospects": len(prospects),
            "events": len(events),
            "candidates": len(candidates_data),
        },
        "excluded_counts": dict(sorted(excluded_counts.items())),
        "withheld_counts": dict(sorted(withheld_counts.items())),
        "company_usage": company_usage,
        "research_queue": research_queue,
        "invitations": rows_out,
        "event_plan": event_plan,
    }
    output["output_digest"] = _stable_hash(output)
    return output
