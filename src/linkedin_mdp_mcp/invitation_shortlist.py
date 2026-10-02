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
class CurrentEmployer:
    """Represent one exact profile's owner-approved latest-known employer."""

    profile_url: str
    company_id: str
    citations: tuple[Citation, ...]


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


def _citation_list(
    value: Any, now: datetime, *, max_age: timedelta, check_source_date: bool = True
) -> tuple[Citation, ...]:
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
        if check_source_date and source_date is not None and (source_date > now.date() or (now.date() - source_date).days > max_age.days):
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


def _current_employers(
    value: Any, registry: Mapping[str, Company], now: datetime
) -> tuple[dict[str, CurrentEmployer], list[dict[str, Any]]]:
    """Validate cited latest-employer assignments and queue unresolved exact identities."""
    assignments: dict[str, CurrentEmployer] = {}
    queue: list[dict[str, Any]] = []
    for index, (raw_url, raw_assignment) in enumerate(_object(value, "current_employers_invalid").items()):
        profile_url = normalize_profile_url(raw_url)
        key_json = json.dumps(raw_url, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        evidence_id = f"current-employer-key-sha256:{hashlib.sha256(key_json.encode('ascii')).hexdigest()}"
        pointer = {
            "evidence_id": evidence_id,
            "opaque_recipient_id": evidence_id,
            "source_ref": f"qualification.current_employers.keys[{index}]",
            "profile_url": profile_url,
        }
        if profile_url is None:
            queue.append({**pointer, "reason": "current_employer_identity_invalid"})
            continue
        if not isinstance(raw_assignment, Mapping):
            queue.append({**pointer, "reason": "current_employer_assignment_invalid"})
            continue
        assignment = raw_assignment
        company_id = assignment.get("company_id")
        if not isinstance(company_id, str) or company_id not in registry:
            queue.append({**pointer, "reason": "current_employer_unregistered"})
            continue
        try:
            citations = _citation_list(
                assignment.get("citations"), now, max_age=_QUALIFICATION_MAX_AGE, check_source_date=False
            )
        except ShortlistInputError as exc:
            queue.append({**pointer, "reason": exc.code})
            continue
        existing = assignments.get(profile_url)
        if existing and existing.company_id != company_id:
            queue.append({**pointer, "reason": "conflicting_current_employer_assignments"})
            assignments.pop(profile_url, None)
            continue
        if existing is None:
            assignments[profile_url] = CurrentEmployer(profile_url, company_id, citations)
        else:
            assignments[profile_url] = CurrentEmployer(profile_url, company_id, (*existing.citations, *citations))
    unresolved = {item["profile_url"] for item in queue if item["profile_url"] is not None}
    for profile_url in unresolved:
        assignments.pop(profile_url, None)
    return assignments, queue


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
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
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
        elif semantics == "actual" and precision == "date" and event_at is not None and isinstance(source_time, str):
            event_date = datetime.fromisoformat(source_time.strip()).date()
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
        dates = {
            _stable_hash(value): value
            for key, value in attributes.items()
            if isinstance(key, str) and _key(key) == "invite_sent_date" and value is not None and value != ""
        }
        sent_date = next(iter(dates.values())) if len(dates) == 1 else None
        event_date: date | None = None
        if isinstance(sent_date, str):
            try:
                event_date = _date(sent_date, "crm_invitation_date_invalid")
            except ShortlistInputError:
                pass
        evidence_id = _history_evidence_id("crm-invitation", [row.get("id"), profile_url, sent_date if len(dates) <= 1 else sorted(dates)])
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
    """Exclude affirmative or ambiguous suppression flags and explicit terminal statuses."""
    for raw_key, value in attributes.items():
        if not isinstance(raw_key, str):
            continue
        key = _key(raw_key)
        if key in {"opt_out", "opted_out", "do_not_contact", "suppressed", "linkedin_opt_out"}:
            if value is None or value is False or value == 0:
                continue
            if isinstance(value, str) and value.strip().casefold() in {"", "false", "no", "0"}:
                continue
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
    if result["company_usage"]:
        lines.extend(["## Lifetime company capacity", ""])
        employers = result["current_employer_assignments"]
        for company_id, usage in result["company_usage"].items():
            count = usage["recorded"] if usage["recorded"] is not None else "withheld"
            remaining = usage["remaining_slots"] if usage["remaining_slots"] is not None else "unknown"
            lines.append(f"{company_id}: {count} recorded, {remaining} slots remaining.")
            for employer in employers:
                if employer["company_id"] == company_id:
                    citations = ", ".join(item["url"] for item in employer["citations"])
                    lines.append(f"- Current employer for {employer['profile_url']}: {citations}")
            for evidence in usage["evidence"]:
                lines.append(f"- {evidence['profile_url']}: {evidence['evidence_id']} at {evidence['source_ref']} (date audit: {evidence['event_date'] or 'unknown'}).")
            lines.append("")
    if result["research_queue"]:
        lines.extend(["## Current employer assignments to resolve", "", "Resolve every queued identity or employer before using all-history capacity counts.", ""])
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
    lines.extend(["## Audit", "", f"Score version: `{_SCORE_VERSION}`. Current employers use the owner-approved latest-known-present assumption. No invitation was sent.", ""])
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
    employers, employer_queue = _current_employers(qualification.get("current_employers"), registry, now)
    prospect_ids_by_url, conflicting_profiles = _source_profile_index(prospects)
    history, connected_profiles, prior_profiles, unresolved_connections = _source_history(provider_rows["INVITATIONS"], provider_rows["CONNECTIONS"], prospects, events)
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
        employer = employers.get(url)
        if employer is None or employer.company_id != candidate_row.company_id:
            withheld_counts["current_employer_conflict"] += 1
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
    cap_evidence_by_company: dict[str, list[dict[str, Any]]] = defaultdict(list)
    history_queue: list[dict[str, Any]] = []
    for evidence in unresolved_connections:
        history_queue.append({
            "opaque_recipient_id": evidence.evidence_id if evidence.profile_url is None else _profile_queue_id(evidence.profile_url),
            "evidence_id": evidence.evidence_id,
            "profile_url": None,
            "source_ref": evidence.source_ref,
            "event_date": evidence.event_date.isoformat() if evidence.event_date else None,
            "source_time": evidence.source_time,
            "time_precision": evidence.time_precision,
            "timezone": evidence.timezone,
            "reason": "unsupported_connected_profile_url" if evidence.source_ref.startswith("snapshots.CONNECTIONS") else "unsupported_historical_profile_url",
        })
    history_by_profile: dict[str, list[HistoryEvidence]] = defaultdict(list)
    for evidence in history:
        if evidence.profile_url is None:
            history_queue.append({
                "opaque_recipient_id": evidence.evidence_id,
                "evidence_id": evidence.evidence_id,
                "profile_url": None,
                "source_ref": evidence.source_ref,
                "event_date": evidence.event_date.isoformat() if evidence.event_date else None,
                "source_time": evidence.source_time,
                "time_precision": evidence.time_precision,
                "timezone": evidence.timezone,
                "reason": "unsupported_historical_profile_url",
            })
        else:
            history_by_profile[evidence.profile_url].append(evidence)
    for profile_url, evidence_rows in history_by_profile.items():
        employer = employers.get(profile_url)
        if employer is None:
            history_queue.extend({
                "opaque_recipient_id": _profile_queue_id(profile_url),
                "evidence_id": evidence.evidence_id,
                "profile_url": profile_url,
                "source_ref": evidence.source_ref,
                "event_date": evidence.event_date.isoformat() if evidence.event_date else None,
                "source_time": evidence.source_time,
                "time_precision": evidence.time_precision,
                "timezone": evidence.timezone,
                "reason": "current_employer_missing",
            } for evidence in evidence_rows)
            continue
        company_counts[employer.company_id] += 1
        cap_evidence_by_company[employer.company_id].extend({
            "evidence_id": evidence.evidence_id,
            "profile_url": profile_url,
            "source_ref": evidence.source_ref,
            "event_date": evidence.event_date.isoformat() if evidence.event_date else None,
            "source_time": evidence.source_time,
            "time_precision": evidence.time_precision,
            "timezone": evidence.timezone,
        } for evidence in evidence_rows)
    history_queue.extend(employer_queue)
    research_queue = history_queue
    unique_queue: list[dict[str, Any]] = []
    seen_queue: set[tuple[str, str]] = set()
    for queue_item in research_queue:
        key = (queue_item.get("evidence_id", queue_item.get("profile_url", "")), queue_item["reason"])
        if key not in seen_queue:
            unique_queue.append(queue_item)
            seen_queue.add(key)
    research_queue = unique_queue

    if research_queue:
        status = "withheld_current_employer_assignments"
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
    research_queue.sort(key=lambda item: (item.get("evidence_id", ""), item["reason"]))
    rows_out = [_candidate_json(item) for item in selected]
    company_usage: dict[str, dict[str, Any]] = {}
    for company_id in sorted(registry):
        usage: dict[str, Any] = {
            "recorded": company_counts[company_id] if status == "ready" else None,
            "remaining_slots": max(0, 3 - company_counts[company_id]) if status == "ready" else None,
            "evidence": cap_evidence_by_company[company_id],
        }
        company_usage[company_id] = usage
    output: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "cap_scope": cap_scope,
        "score_version": _SCORE_VERSION,
        "evidence_validation": "current employer is an owner-approved latest-known-present assumption; citations are retained but external page contents are not fetched",
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
        "current_employer_assignments": [
            {
                "profile_url": item.profile_url,
                "company_id": item.company_id,
                "citations": [_citation_json(citation) for citation in item.citations],
            }
            for item in sorted(employers.values(), key=lambda item: item.profile_url)
        ],
        "company_usage": company_usage,
        "research_queue": research_queue,
        "invitations": rows_out,
    }
    output["output_digest"] = _stable_hash(output)
    return output
