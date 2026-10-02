"""Exercise invitation shortlisting through validated inputs and the private CLI."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from linkedin_mdp_mcp.invitation_shortlist import (
    ShortlistInputError,
    _history_evidence_id,
    build_invitation_shortlist,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
PROFILE_A = "https://www.linkedin.com/in/person-a"
PROFILE_B = "https://www.linkedin.com/in/person-b"
PROFILE_C = "https://www.linkedin.com/in/person-c"


def citation(path: str, *, days: int = 2) -> dict[str, str]:
    """Return one synthetic, dated citation without real person data."""
    return {
        "url": f"https://evidence.example/{path}",
        "source_date": (NOW - timedelta(days=days)).date().isoformat(),
        "retrieved_at": (NOW - timedelta(hours=1)).isoformat(),
    }


def prospect_row(profile_url: str, prospect_id: str) -> dict[str, Any]:
    """Return one exact CRM prospect fixture with no outreach history."""
    return {"id": prospect_id, "linkedin_url": profile_url, "attributes": {}}


def source_export(
    *,
    connections: list[dict[str, Any]] | None = None,
    invitations: list[dict[str, Any]] | None = None,
    prospects: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    collected_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a complete collector envelope with matching retained raw pages."""
    def provider(domain: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Retain the synthetic provider elements and exported rows."""
        return {
            "domain": domain,
            "source_result": "success",
            "completed_at": NOW.isoformat(),
            "provider_generated_at": None,
            "page_count": 1,
            "truncated": False,
            "raw_elements": [{"snapshotDomain": domain, "snapshotData": deepcopy(rows)}],
            "rows": deepcopy(rows),
        }

    def database(rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Retain a complete synthetic database table."""
        return {
            "source_result": "success",
            "completed_at": NOW.isoformat(),
            "consistency": "stable_count_and_unique_ids",
            "page_count": 1,
            "row_count": len(rows),
            "truncated": False,
            "rows": deepcopy(rows),
        }

    return {
        "schema_version": 1,
        "collected_at": (collected_at or NOW - timedelta(minutes=5)).isoformat(),
        "snapshots": {
            "CONNECTIONS": provider("CONNECTIONS", connections or []),
            "INVITATIONS": provider("INVITATIONS", invitations or []),
            "INBOX": provider("INBOX", []),
        },
        "prospects": database([prospect_row(PROFILE_A, "tracked-a")] if prospects is None else prospects),
        "events": database(events or []),
    }


def qualification(
    *,
    candidates: list[dict[str, Any]] | None = None,
    history_bindings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build cited synthetic qualification evidence and verified company aliases."""
    return {
        "schema_version": 1,
        "company_registry": [
            {"company_id": "co-a", "name": "PVF Company", "aliases": ["PVF Company", "PVF Co"]},
            {"company_id": "co-b", "name": "Second PVF Company", "aliases": ["Second PVF Company"]},
        ],
        "candidates": deepcopy(candidates if candidates is not None else [candidate(PROFILE_A)]),
        "history_company_bindings": deepcopy(history_bindings or []),
    }


def candidate(
    profile_url: str,
    *,
    name: str = "Synthetic Person",
    company_id: str = "co-a",
    company_name: str = "PVF Company",
    factors: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a fully cited, exact-identity synthetic PVF candidate."""
    return {
        "profile_url": profile_url,
        "identity": {
            "name": {"value": name, "citations": [citation("identity")]},
            "current_role": {"value": "Synthetic Buyer", "citations": [citation("role")]},
            "country": {"value": "US", "citations": [citation("country")]},
            "pvf_employer": {
                "value": True,
                "company_id": company_id,
                "company_name": company_name,
                "citations": [citation("employer")],
            },
        },
        "rank_factors": deepcopy(factors or {}),
    }


def provider_timestamp(value: datetime) -> str:
    """Format the collector's provider-local invitation timestamp fixture."""
    hour = value.hour % 12 or 12
    return f"{value.month}/{value.day}/{value.year % 100:02d}, {hour}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"


def invitation(profile_url: str) -> dict[str, Any]:
    """Return one outgoing provider invitation row."""
    return {"Direction": "OUTGOING", "inviteeProfileUrl": profile_url, "Sent At": provider_timestamp(NOW)}


def history_binding(profile_url: str, source_row: dict[str, Any], company_id: str = "co-a") -> dict[str, Any]:
    """Bind a historical invitee to cited company evidence at invitation time."""
    parsed = time.strptime(source_row["Sent At"], "%m/%d/%y, %I:%M %p")
    event_date = date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
    event_citation = citation("historical-employer", days=(NOW.date() - event_date).days)
    return {
        "evidence_id": _history_evidence_id("invitation", source_row),
        "profile_url": profile_url,
        "company_id": company_id,
        "company_at_event": True,
        "event_date": event_date.isoformat(),
        "employment_interval": {"start_date": (event_date - timedelta(days=365)).isoformat(), "end_date": event_date.isoformat()},
        "citations": [event_citation],
    }


def crm_history_binding(profile_url: str, prospect_id: str, event_date: str, company_id: str = "co-a") -> dict[str, Any]:
    """Bind one dated CRM send to cited company evidence at its exact event date."""
    event_day = datetime.fromisoformat(event_date).date()
    return {
        "evidence_id": _history_evidence_id("crm-invitation", [prospect_id, profile_url, event_date]),
        "profile_url": profile_url,
        "company_id": company_id,
        "company_at_event": True,
        "event_date": event_date,
        "employment_interval": {"start_date": (event_day - timedelta(days=365)).isoformat(), "end_date": event_date},
        "citations": [citation("historical-crm-employer", days=(NOW.date() - event_day).days)],
    }


def run(source: dict[str, Any], qualification_data: dict[str, Any], *, cap_scope: str = "all_history") -> dict[str, Any]:
    """Run the pure shortlist decision with a fixed, timezone-aware clock."""
    return build_invitation_shortlist(source, qualification_data, cap_scope=cap_scope, now=NOW)


def test_withdrawn_history_still_excludes_profile_after_thirty_one_days() -> None:
    """Historical invitation evidence excludes a profile regardless of withdrawal age."""
    old = NOW - timedelta(days=31)
    row = invitation(PROFILE_A)
    row["Sent At"] = provider_timestamp(old)
    result = run(source_export(invitations=[row]), qualification(history_bindings=[history_binding(PROFILE_A, row)]))
    assert result["invitations"] == []
    assert result["excluded_counts"]["previously_invited"] == 1


def test_collector_pages_allow_multiple_elements_and_duplicate_raw_rows() -> None:
    """Transport coverage accepts multiple retained elements and generic-client deduplication."""
    source = source_export()
    snapshot = source["snapshots"]["CONNECTIONS"]
    snapshot["page_count"] = 2
    snapshot["raw_elements"] = [
        {"snapshotDomain": "CONNECTIONS", "snapshotData": []},
        {"snapshotDomain": "CONNECTIONS", "snapshotData": []},
    ]
    result = run(source, qualification())
    assert result["status"] == "ready"


def test_collector_rows_must_be_present_in_raw_pages_but_may_be_deduplicated() -> None:
    """Raw page evidence may repeat a row while the generic snapshot exports it once."""
    source = source_export(connections=[{"URL": PROFILE_C}])
    snapshot = source["snapshots"]["CONNECTIONS"]
    snapshot["page_count"] = 2
    snapshot["raw_elements"].append({"snapshotDomain": "CONNECTIONS", "snapshotData": [{"URL": PROFILE_C}]})
    result = run(source, qualification())
    assert result["status"] == "ready"
    assert result["source_counts"]["connections"] == 1


def test_company_cap_counts_distinct_profiles_across_verified_aliases() -> None:
    """Repeated dated CRM sends for one exact profile consume one company slot."""
    prospects = [
        prospect_row(PROFILE_A, "tracked-a"),
        {"id": "tracked-1", "linkedin_url": PROFILE_B, "attributes": {"Company": "PVF Co", "Invite Sent Date": "2026-01-01"}},
        {"id": "tracked-2", "linkedin_url": "https://linkedin.com/in/person-b/", "attributes": {"Company": "PVF Company", "Invite Sent Date": "2026-02-01"}},
        {"id": "tracked-3", "linkedin_url": PROFILE_C, "attributes": {"Company": "PVF Company", "Invite Sent Date": "2026-01-02"}},
    ]
    candidates = [candidate(PROFILE_A), candidate(PROFILE_B), candidate(PROFILE_C)]
    bindings = [
        crm_history_binding(PROFILE_B, "tracked-1", "2026-01-01"),
        crm_history_binding(PROFILE_B, "tracked-2", "2026-02-01"),
        crm_history_binding(PROFILE_C, "tracked-3", "2026-01-02"),
    ]
    result = run(source_export(prospects=prospects), qualification(candidates=candidates, history_bindings=bindings))
    assert [item["profile_url"] for item in result["invitations"]] == [PROFILE_A]
    assert result["company_usage"]["co-a"]["recorded"] == 2
    assert result["company_usage"]["co-a"]["remaining_slots"] == 1


def test_all_history_with_missing_employer_binding_withholds_shortlist_and_queues_research() -> None:
    """An unbound prior invite blocks universal cap claims without faking an empty pass."""
    result = run(source_export(invitations=[invitation(PROFILE_B)]), qualification())
    assert result["status"] == "withheld_history_company_bindings"
    assert result["invitations"] == []
    queued = result["research_queue"][0]
    assert queued["reason"] == "company_at_invitation_unbound"
    assert queued["evidence_id"].startswith("history-evidence-sha256:")
    assert queued["profile_url"] == PROFILE_B
    assert queued["source_ref"] == "snapshots.INVITATIONS.rows[0]"
    assert queued["event_date"] == NOW.date().isoformat()
    assert result["company_usage"]["co-a"] == {"recorded": None, "remaining_slots": None}


def test_provider_local_invitation_timestamp_preserves_calendar_day_precision() -> None:
    """The collector's locale timestamp yields a calendar date without a fake timezone."""
    row = invitation(PROFILE_B)
    row["Sent At"] = "9/27/26, 2:20 PM"
    result = run(source_export(invitations=[row]), qualification())
    queued = next(item for item in result["research_queue"] if item["source_ref"] == "snapshots.INVITATIONS.rows[0]")
    assert queued["event_date"] == "2026-09-27"
    assert queued["time_precision"] == "provider_local_day"
    assert queued["timezone"] == "unknown"
    assert queued["source_time"] == "9/27/26, 2:20 PM"
    assert queued["reason"] == "company_at_invitation_unbound"


@pytest.mark.parametrize("timestamp", ["13/27/26, 2:20 PM", "09/27/2026, 2:20 PM", "#REF!"])
def test_unknown_provider_timestamp_formats_remain_unresolved(timestamp: str) -> None:
    """Invalid and unsupported source dates remain unknown instead of guessing a timezone."""
    row = invitation(PROFILE_B)
    row["Sent At"] = timestamp
    result = run(source_export(invitations=[row]), qualification())
    queued = next(item for item in result["research_queue"] if item["source_ref"] == "snapshots.INVITATIONS.rows[0]")
    assert queued["event_date"] is None
    assert queued["reason"] == "invitation_timestamp_unknown"


def test_observation_timestamp_does_not_masquerade_as_invitation_date() -> None:
    """An import or observation timestamp cannot date the historical invitation."""
    event = {
        "id": "event-observed",
        "source": "GOOGLE_SHEETS_CRM_BASELINE",
        "event_type": "LINKEDIN_INVITE_SENT",
        "prospect_id": "tracked-a",
        "occurred_at": NOW.isoformat(),
        "payload": {"timestamp_precision": "date", "timestamp_semantics": "observed_at"},
    }
    result = run(source_export(events=[event]), qualification())
    queued = next(item for item in result["research_queue"] if item["source_ref"] == "events.rows[0]")
    assert queued["event_date"] is None
    assert queued["reason"] == "invitation_timestamp_unknown"


def test_explicit_actual_semantics_uses_the_recorded_calendar_day() -> None:
    """Only an event explicitly labeled actual can use its recorded event date."""
    event = {
        "id": "event-actual",
        "source": "GOOGLE_SHEETS_CRM_BASELINE",
        "event_type": "LINKEDIN_INVITE_SENT",
        "prospect_id": "tracked-a",
        "occurred_at": "2026-09-27T00:00:00Z",
        "payload": {"timestamp_precision": "date", "timestamp_semantics": "actual"},
    }
    result = run(source_export(events=[event]), qualification())
    queued = next(item for item in result["research_queue"] if item["source_ref"] == "events.rows[0]")
    assert queued["event_date"] == "2026-09-27"
    assert queued["time_precision"] == "calendar_day"


def test_mdp_history_uses_supported_provider_sent_date_semantics() -> None:
    """MDP history uses its explicitly retained provider send date, not observation time."""
    event = {
        "id": "event-provider-date",
        "source": "LINKEDIN_MDP",
        "event_type": "LINKEDIN_INVITATION_HISTORY_FOUND",
        "prospect_id": "tracked-a",
        "occurred_at": NOW.isoformat(),
        "payload": {
            "provider_sent_at": "9/27/26, 2:20 PM",
            "timestamp_precision": "provider_local_day",
            "timestamp_semantics": "provider_local_time_timezone_unspecified",
        },
    }
    result = run(source_export(events=[event]), qualification())
    queued = next(item for item in result["research_queue"] if item["source_ref"] == "events.rows[0]")
    assert queued["event_date"] == "2026-09-27"
    assert queued["time_precision"] == "provider_local_day"
    assert queued["timezone"] == "unknown"


def test_distinct_history_rows_without_event_ids_keep_distinct_bindings() -> None:
    """Repeated sends without event IDs remain separate cap events by source pointer."""
    base = {
        "source": "GOOGLE_SHEETS_CRM_BASELINE",
        "event_type": "LINKEDIN_INVITE_SENT",
        "prospect_id": "tracked-a",
        "payload": {"timestamp_precision": "date", "timestamp_semantics": "actual"},
    }
    events = [
        {**base, "occurred_at": "2026-09-27T00:00:00Z"},
        {**base, "occurred_at": "2026-09-26T00:00:00Z"},
    ]
    result = run(source_export(events=events), qualification())
    queued = [item for item in result["research_queue"] if item["source_ref"].startswith("events.rows[")]
    assert {item["source_ref"] for item in queued} == {"events.rows[0]", "events.rows[1]"}
    assert len({item["evidence_id"] for item in queued}) == 2


def test_duplicate_prospect_ids_fail_before_event_identity_join() -> None:
    """A duplicated Supabase prospect ID cannot redirect history to another profile."""
    prospects = [prospect_row(PROFILE_A, "duplicate-id"), prospect_row(PROFILE_B, "duplicate-id")]
    with pytest.raises(ShortlistInputError, match="source_prospects_duplicate_id"):
        run(source_export(prospects=prospects), qualification())


def test_bindings_are_per_historical_invitation_event_not_current_employer() -> None:
    """A person invited at two companies consumes one distinct slot at both companies."""
    first = invitation(PROFILE_B)
    first["Sent At"] = provider_timestamp(NOW - timedelta(days=40))
    second = invitation(PROFILE_B)
    second["Sent At"] = provider_timestamp(NOW - timedelta(days=10))
    bindings = [history_binding(PROFILE_B, first, "co-a"), history_binding(PROFILE_B, second, "co-b")]
    result = run(source_export(invitations=[first, second]), qualification(history_bindings=bindings))
    assert result["status"] == "ready"
    assert result["company_usage"]["co-a"]["recorded"] == 1
    assert result["company_usage"]["co-b"]["recorded"] == 1


def test_connection_evidence_excludes_candidate_but_does_not_consume_invite_cap() -> None:
    """Existing connections do not consume a lifetime invitation slot."""
    candidates = [candidate(PROFILE_B), candidate(PROFILE_A), candidate(PROFILE_C), candidate("https://www.linkedin.com/in/person-d")]
    prospects = [prospect_row(url, f"tracked-{index}") for index, url in enumerate((PROFILE_A, PROFILE_C, "https://www.linkedin.com/in/person-d"))]
    result = run(source_export(connections=[{"URL": PROFILE_B, "Company": "PVF Company"}], prospects=prospects), qualification(candidates=candidates))
    assert PROFILE_B not in {item["profile_url"] for item in result["invitations"]}
    assert len(result["invitations"]) == 3
    assert result["company_usage"]["co-a"]["recorded"] == 0


def test_unicode_profile_url_encoding_joins_the_same_identity() -> None:
    """Percent-encoded UTF-8 matches the same exact Unicode profile without transliteration."""
    unicode_url = "https://www.linkedin.com/in/synthetíc-名字-☀"
    encoded_url = "https://linkedin.com/in/synthet%C3%ADc-%E5%90%8D%E5%AD%97-%E2%98%80/"
    result = run(
        source_export(connections=[{"URL": encoded_url}]),
        qualification(candidates=[candidate(unicode_url)]),
    )
    assert result["invitations"] == []
    assert result["excluded_counts"]["connected"] == 1


def test_same_names_do_not_join_profiles() -> None:
    """Two exact URLs remain distinct even when the displayed name is identical."""
    candidates = [candidate(PROFILE_A, name="Same Name"), candidate(PROFILE_B, name="Same Name")]
    result = run(source_export(prospects=[prospect_row(PROFILE_A, "tracked-a"), prospect_row(PROFILE_B, "tracked-b")]), qualification(candidates=candidates))
    assert {item["profile_url"] for item in result["invitations"]} == {PROFILE_A, PROFILE_B}


@pytest.mark.parametrize(
    ("edit", "reason"),
    [
        (lambda value: value.pop("profile_url"), "identity_unresolved"),
        (lambda value: value["identity"].pop("country"), "qualification_missing_country"),
        (lambda value: value["identity"].pop("pvf_employer"), "qualification_missing_pvf_employer"),
        (lambda value: value["identity"]["current_role"]["citations"].clear(), "qualification_missing_citation"),
        (lambda value: value["identity"]["current_role"]["citations"][0].update(source_date="2026-05-01"), "qualification_stale"),
    ],
)
def test_incomplete_or_stale_qualification_is_withheld(edit: Any, reason: str) -> None:
    """Qualification gaps never turn into a ranked candidate."""
    item = candidate(PROFILE_A)
    edit(item)
    result = run(source_export(), qualification(candidates=[item]))
    assert result["invitations"] == []
    assert result["withheld_counts"][reason] == 1


def test_alias_collision_is_rejected_instead_of_heuristically_merged() -> None:
    """The same verified alias cannot map to two company identities."""
    data = qualification()
    data["company_registry"].append({"company_id": "co-c", "name": "Other", "aliases": ["PVF Co"]})
    with pytest.raises(ShortlistInputError, match="company_alias_conflict"):
        run(source_export(), data)


def test_suppressed_prospect_is_excluded() -> None:
    """Explicit opt-out attributes prevent recommendation output."""
    source = source_export(prospects=[{
        "id": "tracked-a", "linkedin_url": PROFILE_A,
        "attributes": {"Opt Out": True},
    }])
    result = run(source, qualification())
    assert result["invitations"] == []
    assert result["excluded_counts"]["suppressed"] == 1


def test_suppression_event_is_excluded_by_exact_profile() -> None:
    """A suppression event follows the exact prospect URL mapping."""
    source = source_export(
        prospects=[{"id": "tracked-a", "linkedin_url": PROFILE_A, "attributes": {}}],
        events=[{"id": "event-a", "prospect_id": "tracked-a", "event_type": "LINKEDIN_OPTED_OUT", "source": "AGENT_ACTION_REPORT", "occurred_at": NOW.isoformat(), "payload": {}}],
    )
    result = run(source, qualification())
    assert result["invitations"] == []
    assert result["excluded_counts"]["suppressed"] == 1


@pytest.mark.parametrize("mutation", ["stale", "truncated", "bad-page-count", "missing-raw-elements", "raw-row-mismatch", "raw-row-omitted", "wrong-domain", "unsupported-url"])
def test_bad_or_incomplete_transport_fails_closed(mutation: str) -> None:
    """Transport gaps are errors and cannot produce a false empty shortlist."""
    source = source_export()
    if mutation == "stale":
        source["collected_at"] = (NOW - timedelta(hours=25)).isoformat()
    elif mutation == "truncated":
        source["snapshots"]["INVITATIONS"]["truncated"] = True
    elif mutation == "bad-page-count":
        source["snapshots"]["CONNECTIONS"]["page_count"] = 0
    elif mutation == "missing-raw-elements":
        source["snapshots"]["CONNECTIONS"]["raw_elements"] = []
    elif mutation == "raw-row-mismatch":
        source["snapshots"]["CONNECTIONS"]["rows"].append({"URL": PROFILE_B})
    elif mutation == "raw-row-omitted":
        source["snapshots"]["CONNECTIONS"]["raw_elements"][0]["snapshotData"] = [{"URL": PROFILE_B}]
    elif mutation == "wrong-domain":
        source["snapshots"]["CONNECTIONS"]["raw_elements"][0]["snapshotDomain"] = "INBOX"
    elif mutation == "unsupported-url":
        source["snapshots"]["CONNECTIONS"]["rows"] = [{"URL": "https://linkedin.com.evil.test/in/person-a"}]
        source["snapshots"]["CONNECTIONS"]["raw_elements"][0]["snapshotData"] = deepcopy(source["snapshots"]["CONNECTIONS"]["rows"])
    if mutation == "unsupported-url":
        result = run(source, qualification())
        assert result["status"] == "withheld_history_company_bindings"
        assert "unsupported_connected_profile_url" in {item["reason"] for item in result["research_queue"]}
    else:
        with pytest.raises(ShortlistInputError):
            run(source, qualification())


def test_empty_complete_snapshot_is_a_valid_empty_shortlist() -> None:
    """A verified empty source differs from missing or truncated input."""
    result = run(source_export(), qualification(candidates=[]))
    assert result["status"] == "ready"
    assert result["invitations"] == []
    assert result["source_counts"]["candidates"] == 0


def test_history_can_be_audited_before_any_company_registry_exists() -> None:
    """An empty qualification registry still yields a safe historical research queue."""
    data = qualification(candidates=[])
    data["company_registry"] = []
    row = invitation(PROFILE_B)
    result = run(source_export(invitations=[row]), data)
    assert result["status"] == "withheld_history_company_bindings"
    assert result["company_usage"] == {}
    assert result["research_queue"]


def test_unknown_rank_factors_remain_unknown_and_all_unknown_score_is_null() -> None:
    """Missing evidence is visible and does not become an inactivity score."""
    result = run(source_export(), qualification())
    row = result["invitations"][0]
    assert row["score"] is None
    assert set(row["unknown_factors"]) == {"activity", "mutual_connections", "connection_count", "profile_photo"}


def test_ranked_factors_carry_citations_and_ties_use_canonical_url() -> None:
    """Evidence-backed factors rank deterministically independent of input order."""
    factor = {"value": True, "observed_at": NOW.isoformat(), "citations": [citation("activity")]}
    candidates = [
        candidate(PROFILE_B, factors={"activity": factor}),
        candidate(PROFILE_A, factors={"activity": factor}),
    ]
    prospects = [prospect_row(PROFILE_A, "tracked-a"), prospect_row(PROFILE_B, "tracked-b")]
    first = run(source_export(prospects=prospects), qualification(candidates=candidates))
    second = run(source_export(prospects=prospects), qualification(candidates=list(reversed(candidates))))
    assert [item["profile_url"] for item in first["invitations"]] == [PROFILE_A, PROFILE_B]
    assert first["output_digest"] == second["output_digest"]
    assert first["invitations"][0]["score_factors"]["activity"]["citations"]


def test_undated_official_citation_uses_retrieval_time_without_inventing_source_date() -> None:
    """An undated source retains a current retrieval date and explicit unknown source date."""
    item = candidate(PROFILE_A)
    item["identity"]["current_role"]["citations"][0]["source_date"] = None
    result = run(source_export(), qualification(candidates=[item]))
    citation_out = result["invitations"][0]["evidence"]["current_role"][0]
    assert citation_out["source_date"] is None
    assert citation_out["retrieved_at"]


def test_alias_company_slot_is_applied_after_rank_and_list_never_exceeds_cap() -> None:
    """Only the strongest eligible candidates receive the remaining company slots."""
    def activity(days: int) -> dict[str, Any]:
        return {"value": True, "observed_at": (NOW - timedelta(days=days)).isoformat(), "citations": [citation(f"activity-{days}", days=days)]}

    candidates = [
        candidate(PROFILE_A, company_name="PVF Co", factors={"activity": activity(2)}),
        candidate(PROFILE_B, company_name="PVF Company", factors={"activity": activity(4)}),
        candidate(PROFILE_C, company_name="PVF Company", factors={"activity": activity(1)}),
        candidate("https://www.linkedin.com/in/person-d", company_name="PVF Company", factors={"activity": activity(3)}),
    ]
    prospects = [
        {"id": f"tracked-{index}", "linkedin_url": f"https://www.linkedin.com/in/old-{index}", "attributes": {"Company": "PVF Co", "Invite Sent Date": f"2026-01-0{index + 1}"}}
        for index in range(3)
    ]
    prospects.extend(prospect_row(item["profile_url"], f"candidate-{index}") for index, item in enumerate(candidates))
    bindings = [crm_history_binding(f"https://www.linkedin.com/in/old-{index}", f"tracked-{index}", f"2026-01-0{index + 1}") for index in range(3)]
    result = run(source_export(prospects=prospects), qualification(candidates=candidates, history_bindings=bindings))
    assert result["invitations"] == []
    assert result["company_usage"]["co-a"]["remaining_slots"] == 0


def test_shortlist_is_limited_to_twenty_five_after_company_slots() -> None:
    """The selection limit applies after ranking and lifetime company capacity."""
    candidates = []
    companies = []
    for company_index in range(10):
        company_id = f"co-{company_index}"
        company_name = f"PVF Company {company_index}"
        companies.append({"company_id": company_id, "name": company_name, "aliases": [company_name]})
        for person_index in range(3):
            suffix = company_index * 3 + person_index
            candidates.append(candidate(
                f"https://www.linkedin.com/in/person-{suffix:02d}",
                name=f"Synthetic {suffix}",
                company_id=company_id,
                company_name=company_name,
                factors={"activity": {"value": True, "observed_at": NOW.isoformat(), "citations": [citation(f"activity-{suffix}")]}},
            ))
    data = qualification(candidates=candidates)
    data["company_registry"] = companies
    live_prospects = [prospect_row(item["profile_url"], f"tracked-{index}") for index, item in enumerate(candidates)]
    result = run(source_export(prospects=live_prospects), data)
    assert len(result["invitations"]) == 25
    assert all(count["remaining_slots"] >= 0 for count in result["company_usage"].values())


def test_real_cap_policy_is_explicit_and_never_defaults_to_crm() -> None:
    """The caller must name the lifetime-cap scope on every invocation."""
    with pytest.raises(TypeError):
        build_invitation_shortlist(source_export(), qualification(), now=NOW)  # type: ignore[call-arg]


def test_cap_scope_rejects_universal_policy_relaxation() -> None:
    """The only supported cap policy is all-history accounting."""
    with pytest.raises(ShortlistInputError, match="cap_scope_invalid"):
        run(source_export(), qualification(), cap_scope="crm")


def test_dated_crm_sent_fact_queues_event_binding_but_acceptance_does_not() -> None:
    """A dated CRM send needs historical company proof; accepted-only state is not a send fact."""
    prospects = [
        prospect_row(PROFILE_A, "candidate-a"),
        {"id": "sent-b", "linkedin_url": PROFILE_B, "attributes": {"Company": "PVF Co", "Invite Sent Date": "2026-08-01"}},
        {"id": "accepted-c", "linkedin_url": PROFILE_C, "attributes": {"Company": "PVF Company", "Accepted Date": "2026-08-02"}},
    ]
    result = run(source_export(prospects=prospects), qualification(candidates=[candidate(PROFILE_B), candidate(PROFILE_C)]), cap_scope="all_history")
    assert result["status"] == "withheld_history_company_bindings"
    assert result["company_usage"]["co-a"] == {"recorded": None, "remaining_slots": None}
    assert result["excluded_counts"]["previously_invited"] == 2
    assert len(result["research_queue"]) == 1
    queued = result["research_queue"][0]
    assert queued["profile_url"] == PROFILE_B
    assert queued["source_ref"] == "prospects.rows[1]"
    assert queued["event_date"] == "2026-08-01"


def test_recommendations_are_event_plans_not_sends() -> None:
    """The output proposes auditable events without calling a send surface."""
    result = run(source_export(), qualification(), cap_scope="all_history")
    planned = result["event_plan"]
    assert len(planned) == 1
    assert planned[0]["source"] == "AGENT_ACTION_REPORT"
    assert planned[0]["event_type"] == "LINKEDIN_INVITATION_RECOMMENDED"
    assert "not fetched" in result["evidence_validation"]
    assert "not fetched" in planned[0]["payload"]["qualification_attestation"]
    assert not any("sent" in key.lower() for key in planned[0])


def test_cli_writes_private_outputs_and_sanitized_stdout() -> None:
    """The private CLI emits only fixed status text and creates mode-0600 files."""
    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        source_path = temp_path / "source.json"
        qualification_path = temp_path / "qualification.json"
        output_path = temp_path / "out"
        source_path.write_text(json.dumps(source_export()), encoding="utf-8")
        qualification_path.write_text(json.dumps(qualification()), encoding="utf-8")
        os.chmod(source_path, 0o600)
        os.chmod(qualification_path, 0o600)
        process = subprocess.run(
            [
                sys.executable, str(ROOT / "scripts" / "build_invitation_shortlist.py"),
                "--source", str(source_path), "--qualification", str(qualification_path),
                "--output-dir", str(output_path), "--cap-scope", "all_history",
                "--now", NOW.isoformat(),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert process.returncode == 0
        assert process.stdout.strip() == "invitation shortlist: report written"
        assert process.stderr == ""
        report_path = output_path / "invitation-shortlist.json"
        markdown_path = output_path / "invitation-shortlist.md"
        assert report_path.exists() and markdown_path.exists()
        assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(markdown_path.stat().st_mode) == 0o600
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["invitations"][0]["profile_url"] == PROFILE_A
        assert "Synthetic Person" in markdown_path.read_text(encoding="utf-8")
        assert not any(name in process.stdout for name in ("Synthetic Person", PROFILE_A))


def test_cli_refuses_to_overwrite_existing_outputs() -> None:
    """Existing report artifacts remain untouched on a repeated run."""
    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        source_path = temp_path / "source.json"
        qualification_path = temp_path / "qualification.json"
        output_path = temp_path / "out"
        output_path.mkdir()
        source_path.write_text(json.dumps(source_export()), encoding="utf-8")
        qualification_path.write_text(json.dumps(qualification()), encoding="utf-8")
        source_path.chmod(0o600)
        qualification_path.chmod(0o600)
        (output_path / "invitation-shortlist.json").write_text("sentinel", encoding="utf-8")
        process = subprocess.run(
            [
                sys.executable, str(ROOT / "scripts" / "build_invitation_shortlist.py"),
                "--source", str(source_path), "--qualification", str(qualification_path),
                "--output-dir", str(output_path), "--cap-scope", "all_history", "--now", NOW.isoformat(),
            ], cwd=ROOT, capture_output=True, text=True, check=False,
        )
        assert process.returncode == 1
        assert process.stdout.strip() == "invitation shortlist: failed"
        assert (output_path / "invitation-shortlist.json").read_text(encoding="utf-8") == "sentinel"
