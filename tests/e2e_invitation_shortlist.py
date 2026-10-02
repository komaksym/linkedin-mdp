"""Exercise invitation shortlisting through validated inputs and the private CLI."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from linkedin_mdp_mcp.invitation_shortlist import (
    ShortlistInputError,
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
    current_employers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build cited synthetic qualification evidence and verified company aliases."""
    candidate_rows = deepcopy(candidates if candidates is not None else [candidate(PROFILE_A)])
    return {
        "schema_version": 1,
        "company_registry": [
            {"company_id": "co-a", "name": "PVF Company", "aliases": ["PVF Company", "PVF Co"]},
            {"company_id": "co-b", "name": "Second PVF Company", "aliases": ["Second PVF Company"]},
        ],
        "candidates": candidate_rows,
        "current_employers": deepcopy(current_employers) if current_employers is not None else {
            item["profile_url"]: {
                "company_id": item["identity"]["pvf_employer"]["company_id"],
                "citations": item["identity"]["pvf_employer"]["citations"],
            }
            for item in candidate_rows
            if item.get("profile_url") and isinstance(item.get("identity"), dict)
            and isinstance(item["identity"].get("pvf_employer"), dict)
        },
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


def run(source: dict[str, Any], qualification_data: dict[str, Any], *, cap_scope: str = "all_history") -> dict[str, Any]:
    """Run the pure shortlist decision with a fixed, timezone-aware clock."""
    return build_invitation_shortlist(source, qualification_data, cap_scope=cap_scope, now=NOW)


def test_withdrawn_history_still_excludes_profile_after_thirty_one_days() -> None:
    """Historical invitation evidence excludes a profile regardless of withdrawal age."""
    old = NOW - timedelta(days=31)
    row = invitation(PROFILE_A)
    row["Sent At"] = provider_timestamp(old)
    result = run(source_export(invitations=[row]), qualification())
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
    employer_map = {
        item["profile_url"]: {"company_id": "co-a", "citations": [citation("current-employer")]}
        for item in candidates
    }
    employer_map.update({
        url: {"company_id": "co-a", "citations": [citation("current-employer-alias")]}
        for url in (PROFILE_B, "https://linkedin.com/in/person-b/", PROFILE_C)
    })
    result = run(source_export(prospects=prospects), qualification(candidates=candidates, current_employers=employer_map))
    assert [item["profile_url"] for item in result["invitations"]] == [PROFILE_A]
    assert result["company_usage"]["co-a"]["recorded"] == 2
    assert result["company_usage"]["co-a"]["remaining_slots"] == 1


def test_all_history_with_missing_employer_binding_withholds_shortlist_and_queues_research() -> None:
    """An unbound prior invite blocks universal cap claims without faking an empty pass."""
    result = run(source_export(invitations=[invitation(PROFILE_B)]), qualification())
    assert result["status"] == "withheld_current_employer_assignments"
    assert result["invitations"] == []
    queued = result["research_queue"][0]
    assert queued["reason"] == "current_employer_missing"
    assert queued["evidence_id"].startswith("history-evidence-sha256:")
    assert queued["profile_url"] == PROFILE_B
    assert queued["source_ref"] == "snapshots.INVITATIONS.rows[0]"
    assert queued["event_date"] == NOW.date().isoformat()
    assert result["company_usage"]["co-a"]["recorded"] is None
    assert result["company_usage"]["co-a"]["remaining_slots"] is None


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
    assert queued["reason"] == "current_employer_missing"


@pytest.mark.parametrize("timestamp", ["13/27/26, 2:20 PM", "09/27/2026, 2:20 PM", "#REF!"])
def test_unknown_provider_timestamp_formats_remain_unresolved(timestamp: str) -> None:
    """Invalid and unsupported source dates remain unknown instead of guessing a timezone."""
    row = invitation(PROFILE_B)
    row["Sent At"] = timestamp
    result = run(source_export(invitations=[row]), qualification())
    queued = next(item for item in result["research_queue"] if item["source_ref"] == "snapshots.INVITATIONS.rows[0]")
    assert queued["event_date"] is None
    assert queued["reason"] == "current_employer_missing"


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
    audited = result["company_usage"]["co-a"]["evidence"][0]
    assert audited["source_ref"] == "events.rows[0]"
    assert audited["event_date"] is None
    assert audited["time_precision"] == "unknown"
    assert audited["timezone"] == "unknown"


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
    audited = result["company_usage"]["co-a"]["evidence"][0]
    assert audited["source_ref"] == "events.rows[0]"
    assert audited["event_date"] == "2026-09-27"
    assert audited["time_precision"] == "calendar_day"
    assert audited["timezone"] == "unknown"


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
    audited = result["company_usage"]["co-a"]["evidence"][0]
    assert audited["source_ref"] == "events.rows[0]"
    assert audited["event_date"] == "2026-09-27"
    assert audited["time_precision"] == "provider_local_day"
    assert audited["timezone"] == "unknown"


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
    audited = result["company_usage"]["co-a"]["evidence"]
    assert {item["source_ref"] for item in audited} == {"events.rows[0]", "events.rows[1]"}
    assert len({item["evidence_id"] for item in audited}) == 2


def test_duplicate_prospect_ids_fail_before_event_identity_join() -> None:
    """A duplicated Supabase prospect ID cannot redirect history to another profile."""
    prospects = [prospect_row(PROFILE_A, "duplicate-id"), prospect_row(PROFILE_B, "duplicate-id")]
    with pytest.raises(ShortlistInputError, match="source_prospects_duplicate_id"):
        run(source_export(prospects=prospects), qualification())


def test_current_employer_owns_all_historical_invitation_dates() -> None:
    """Historical employer clues do not replace the latest-known employer assumption."""
    first = invitation(PROFILE_B)
    first["Sent At"] = provider_timestamp(NOW - timedelta(days=40))
    second = invitation(PROFILE_B)
    second["Sent At"] = provider_timestamp(NOW - timedelta(days=10))
    data = qualification(current_employers={PROFILE_B: {"company_id": "co-b", "citations": [citation("latest-employer")]}})
    result = run(source_export(invitations=[first, second]), data)
    assert result["status"] == "ready"
    assert result["company_usage"]["co-a"]["recorded"] == 0
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
        source["snapshots"]["CONNECTIONS"]["rows"] = [{"URL": PROFILE_B}]
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
        assert result["status"] == "withheld_current_employer_assignments"
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
    assert result["status"] == "withheld_current_employer_assignments"
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
    employer_map = {
        item["profile_url"]: {"company_id": item["identity"]["pvf_employer"]["company_id"], "citations": item["identity"]["pvf_employer"]["citations"]}
        for item in candidates
    }
    employer_map.update({
        f"https://www.linkedin.com/in/old-{index}": {"company_id": "co-a", "citations": [citation(f"old-{index}")]}
        for index in range(3)
    })
    result = run(source_export(prospects=prospects), qualification(candidates=candidates, current_employers=employer_map))
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
    assert result["status"] == "ready"
    assert result["company_usage"]["co-a"]["recorded"] == 1
    assert result["excluded_counts"]["previously_invited"] == 2
    assert result["research_queue"] == []


def test_recommendations_are_read_only_report_rows() -> None:
    """The output does not construct database persistence events."""
    result = run(source_export(), qualification(), cap_scope="all_history")
    assert result["invitations"]
    assert "event_plan" not in result
    assert "external_key" not in json.dumps(result)


def test_cli_writes_private_outputs_and_sanitized_stdout() -> None:
    """The private CLI emits only fixed status text and creates mode-0600 files."""
    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        source_path = temp_path / "source.json"
        qualification_path = temp_path / "qualification.json"
        output_path = temp_path / "out"
        source = source_export()
        created_at = "2026-09-30T14:12:13.123456+02:00"
        source["prospects"]["rows"][0]["created_at"] = created_at
        source_path.write_text(json.dumps(source), encoding="utf-8")
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
        row = report["invitations"][0]
        assert row["date_added"] == created_at
        assert row["date_added_source"] == {
            "prospect_id": "tracked-a",
            "source_ref": "prospects.rows[0].created_at",
            "reason": None,
        }
        markdown = markdown_path.read_text(encoding="utf-8")
        assert "Synthetic Person" in markdown
        assert f"Date added: {created_at}" in markdown
        assert "prospects.rows[0].created_at" in markdown
        assert not any(name in process.stdout for name in ("Synthetic Person", PROFILE_A))
        digest_input = dict(report)
        digest = digest_input.pop("output_digest")
        assert digest == hashlib.sha256(
            json.dumps(digest_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


def test_date_added_uses_exact_canonical_source_match_and_never_changes_ranking() -> None:
    """An exact URL alias may enrich output, but its source time cannot affect selection."""
    source = source_export()
    source["prospects"]["rows"][0]["linkedin_url"] = "https://linkedin.com/in/person-a/"
    created_at = "2026-09-30T14:12:13.123456+02:00"
    source["prospects"]["rows"][0]["created_at"] = created_at
    qualification_data = qualification()
    qualification_data["candidates"][0]["date_added"] = "2099-01-01T00:00:00Z"

    result = run(source, qualification_data)
    without_source_time = source_export()
    without_source_time["prospects"]["rows"][0]["linkedin_url"] = "https://linkedin.com/in/person-a/"
    baseline = run(without_source_time, qualification())

    assert [item["profile_url"] for item in result["invitations"]] == [
        item["profile_url"] for item in baseline["invitations"]
    ]
    assert result["invitations"][0]["score"] == baseline["invitations"][0]["score"]
    assert result["invitations"][0]["date_added"] == created_at
    assert result["invitations"][0]["date_added_source"]["prospect_id"] == "tracked-a"


@pytest.mark.parametrize(
    ("created_at", "reason"),
    [
        (None, "missing"),
        (42, "invalid_timestamp"),
        ("not-a-timestamp", "invalid_timestamp"),
        ("2026-09-30T14:12:13", "invalid_timestamp"),
        ("2026-10-03T00:00:00Z", "future_timestamp"),
    ],
)
def test_invalid_or_future_date_added_stays_unknown_with_safe_pointer(created_at: Any, reason: str) -> None:
    """Invalid values stay unknown and never leak their raw timestamp into output."""
    source = source_export()
    source["prospects"]["rows"][0]["created_at"] = created_at

    result = run(source, qualification())
    row = result["invitations"][0]

    assert row["date_added"] is None
    assert row["date_added_source"] == {
        "prospect_id": "tracked-a",
        "source_ref": "prospects.rows[0].created_at",
        "reason": reason,
    }
    assert str(created_at) not in json.dumps(row)


def test_missing_date_added_stays_unknown_and_does_not_change_membership() -> None:
    """A missing creation time is not inferred and cannot alter eligibility."""
    source = source_export()
    baseline = run(source, qualification())
    expected_membership = [item["profile_url"] for item in baseline["invitations"]]

    source["prospects"]["rows"][0].pop("created_at", None)
    result = run(source, qualification())

    assert [item["profile_url"] for item in result["invitations"]] == expected_membership
    assert result["invitations"][0]["date_added"] is None
    assert result["invitations"][0]["date_added_source"]["reason"] == "missing"


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


@pytest.mark.parametrize("domain", ["CONNECTIONS", "INVITATIONS", "INBOX"])
def test_complete_snapshot_without_elements_is_valid(domain: str) -> None:
    """A fetched empty provider page is complete without any snapshot elements."""
    source = source_export()
    source["snapshots"][domain]["raw_elements"] = []
    assert run(source, qualification())["status"] == "ready"


@pytest.mark.parametrize("key", ["invite sent date", "invite_sent_date", "INVITE SENT DATE"])
def test_normalized_crm_send_date_retains_binding(key: str) -> None:
    """Every recognized spelling of a CRM date remains outgoing evidence regardless of date proof."""
    prospects = [prospect_row(PROFILE_A, "tracked-a"), {"id": "tracked-b", "linkedin_url": PROFILE_B, "attributes": {key: "2026-09-27"}}]
    employers = {PROFILE_A: {"company_id": "co-a", "citations": [citation("latest-a")]}, PROFILE_B: {"company_id": "co-a", "citations": [citation("latest-b")]}}
    result = run(source_export(prospects=prospects), qualification(current_employers=employers))
    assert result["status"] == "ready"
    assert result["company_usage"]["co-a"]["recorded"] == 1


def test_conflicting_normalized_crm_dates_remain_unknown() -> None:
    """Conflicting CRM dates do not affect latest-employer capacity accounting."""
    row = prospect_row(PROFILE_B, "tracked-b")
    row["attributes"] = {"Invite Sent Date": "2026-09-27", "invite_sent_date": "2026-09-28"}
    employer = {PROFILE_B: {"company_id": "co-a", "citations": [citation("latest-b")]}}
    result = run(source_export(prospects=[row]), qualification(current_employers=employer))
    assert result["status"] == "ready"
    assert result["company_usage"]["co-a"]["evidence"][0]["event_date"] is None


@pytest.mark.parametrize("value", ["true", " TRUE ", "yes", "1", 1, "unrecognized"])
def test_text_and_unknown_opt_out_flags_exclude_prospects(value: Any) -> None:
    """Imported affirmative or uninterpretable opt-out flags cannot permit recommendations."""
    row = prospect_row(PROFILE_A, "tracked-a")
    row["attributes"] = {"Opt Out": value}
    result = run(source_export(prospects=[row]), qualification())
    assert result["invitations"] == []
    assert result["excluded_counts"]["suppressed"] == 1


@pytest.mark.parametrize("value", [False, "false", " NO ", "0", 0, "", None])
def test_negative_or_empty_opt_out_flags_do_not_suppress(value: Any) -> None:
    """Explicit negative and absent CRM flags do not invent an opt-out."""
    row = prospect_row(PROFILE_A, "tracked-a")
    row["attributes"] = {"Do Not Contact": value}
    assert len(run(source_export(prospects=[row]), qualification())["invitations"]) == 1


@pytest.mark.parametrize("timestamp", ["2026-09-27T00:00:00+05:00", "2026-09-27T23:00:00-05:00"])
def test_date_precision_events_preserve_source_calendar_day(timestamp: str) -> None:
    """Date-only evidence retains its calendar day across either UTC midnight boundary."""
    event = {"id": "date-event", "prospect_id": "tracked-b", "source": "CRM", "event_type": "LINKEDIN_INVITE_SENT", "occurred_at": timestamp, "payload": {"timestamp_semantics": "actual", "timestamp_precision": "date"}}
    prospects = [prospect_row(PROFILE_A, "tracked-a"), prospect_row(PROFILE_B, "tracked-b")]
    result = run(source_export(prospects=prospects, events=[event]), qualification())
    queued = result["research_queue"][0]
    assert queued["event_date"] == "2026-09-27"
    assert queued["time_precision"] == "calendar_day"
    assert queued["timezone"] == "unknown"


def test_private_writer_preserves_real_filesystem_failure() -> None:
    """Opening, wrapper construction, writing, flushing and cleanup can fail; cleanup must not mask the original error."""
    with tempfile.TemporaryDirectory() as temp_dir:
        output = Path(temp_dir) / "limited.txt"
        script = '''import errno
import resource
import signal
import sys
from pathlib import Path
from scripts.build_invitation_shortlist import _write_new_private
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (16, 16))
try:
    _write_new_private(Path(sys.argv[1]), "x" * 64)
except OSError as exc:
    assert exc.errno == errno.EFBIG, exc.errno
else:
    raise AssertionError("filesystem failure not observed")
'''
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + str(ROOT)
        result = subprocess.run([sys.executable, "-c", script, str(output)], env=environment, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert output.exists()
        assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_present_employer_counts_undated_all_history_once_and_keeps_audit_pointers() -> None:
    """Undated invitation, CRM, and event evidence count once at latest employer with source audit retained."""
    invite = {"Direction": "OUTGOING", "inviteeProfileUrl": PROFILE_B}
    prospects = [prospect_row(PROFILE_A, "tracked-a"), {
        "id": "tracked-b", "linkedin_url": PROFILE_B,
        "attributes": {"Invite Status": "sent", "Company": "Wrong historical CRM label"},
    }]
    events = [{"id": "sent-b", "prospect_id": "tracked-b", "event_type": "LINKEDIN_INVITE_SENT", "source": "CRM", "payload": {}}]
    mapping = {
        PROFILE_A: {"company_id": "co-a", "citations": [citation("candidate-current-employer")]},
        PROFILE_B: {"company_id": "co-a", "citations": [citation("latest-employer")]},
    }
    result = run(source_export(invitations=[invite], prospects=prospects, events=events), qualification(current_employers=mapping))
    assert result["status"] == "ready"
    assert result["company_usage"]["co-a"]["recorded"] == 1
    assert [item["profile_url"] for item in result["invitations"]] == [PROFILE_A]
    evidence = result["company_usage"]["co-a"]["evidence"]
    assert {item["source_ref"] for item in evidence} == {
        "snapshots.INVITATIONS.rows[0]", "prospects.rows[1]", "events.rows[0]",
    }
    assert len({item["evidence_id"] for item in evidence}) == 3
    assert result["company_usage"]["co-a"]["recorded"] == 1


def test_current_employer_conflict_across_canonical_profile_keys_withholds_all() -> None:
    """Different latest-employer mappings for URL aliases cannot produce cap claims."""
    alias = "https://linkedin.com/in/person-b/"
    invite = {"Direction": "OUTGOING", "inviteeProfileUrl": PROFILE_B}
    mapping = {
        PROFILE_B: {"company_id": "co-a", "citations": [citation("employer-a")]},
        alias: {"company_id": "co-b", "citations": [citation("employer-b")]},
    }
    result = run(source_export(invitations=[invite]), qualification(current_employers=mapping))
    assert result["status"] == "withheld_current_employer_assignments"
    assert result["invitations"] == []
    assert result["company_usage"]["co-a"]["recorded"] is None
    assert result["research_queue"][0]["reason"] == "conflicting_current_employer_assignments"


def test_candidate_company_conflict_with_current_employer_is_withheld() -> None:
    """A candidate's qualification company must agree with the owner mapping."""
    mapping = {PROFILE_A: {"company_id": "co-b", "citations": [citation("latest-employer")]}}
    result = run(source_export(), qualification(current_employers=mapping))
    assert result["invitations"] == []
    assert result["withheld_counts"]["current_employer_conflict"] == 1


def test_unknown_historical_identity_or_employer_globally_withholds_capacity() -> None:
    """Unregistered or unsupported invite identities cannot be silently skipped."""
    invite = {"Direction": "OUTGOING", "inviteeProfileUrl": "https://linkedin.com.evil.test/in/person-x"}
    result = run(source_export(invitations=[invite]), qualification())
    assert result["status"] == "withheld_current_employer_assignments"
    assert result["invitations"] == []
    assert result["research_queue"]


def test_read_only_report_has_no_recommendation_event_plan() -> None:
    """Recommendations remain report output and never contain persistence event plans."""
    result = run(source_export(), qualification())
    assert "event_plan" not in result
    assert "external_key" not in json.dumps(result)


@pytest.mark.parametrize("invalid_kind", ["assignment", "company", "citation"])
@pytest.mark.parametrize("invalid_first", [False, True])
def test_invalid_canonical_employer_alias_removes_all_assignment_and_capacity_audit(
    invalid_kind: str, invalid_first: bool
) -> None:
    """Any invalid current-employer alias keeps the exact person unresolved everywhere."""
    valid = {"company_id": "co-a", "citations": [citation("employer-b")]}
    invalid = {
        "assignment": None,
        "company": {"company_id": "unregistered", "citations": [citation("unknown")]},
        "citation": {"company_id": "co-a", "citations": []},
    }[invalid_kind]
    entries = [(PROFILE_B, valid), ("https://linkedin.com/in/person-b/", invalid)]
    if invalid_first:
        entries.reverse()
    employers = {PROFILE_A: {"company_id": "co-a", "citations": [citation("employer-a")]}, **dict(entries)}
    result = run(source_export(invitations=[invitation(PROFILE_B)]), qualification(current_employers=employers))
    assert result["status"] == "withheld_current_employer_assignments"
    assert result["invitations"] == []
    assert all(item["profile_url"] != PROFILE_B for item in result["current_employer_assignments"])
    assert all(item["profile_url"] != PROFILE_B for item in result["company_usage"]["co-a"]["evidence"])
    assert all(item["recorded"] is None for item in result["company_usage"].values())
    assert any(item["profile_url"] == PROFILE_B for item in result["research_queue"])


def test_invalid_employer_identity_keys_keep_distinct_private_queue_pointers() -> None:
    """Unknown mapping keys stay separate and traceable without exposing their raw URLs."""
    employers = {
        PROFILE_A: {"company_id": "co-a", "citations": [citation("employer-a")]},
        "https://linkedin.com.evil.test/in/unknown-a": None,
        "https://linkedin.com.evil.test/in/unknown-b": None,
    }
    result = run(source_export(), qualification(current_employers=employers))
    queued = [item for item in result["research_queue"] if item["reason"] == "current_employer_identity_invalid"]
    assert len(queued) == 2
    assert len({item["evidence_id"] for item in queued}) == 2
    assert len({item["opaque_recipient_id"] for item in queued}) == 2
    assert {item["source_ref"] for item in queued} == {
        "qualification.current_employers.keys[1]", "qualification.current_employers.keys[2]",
    }
    assert "linkedin.com.evil.test" not in json.dumps(queued)
    assert result["status"] == "withheld_current_employer_assignments"


@pytest.mark.parametrize("codepoint", [0xD800, 0xDFFF])
def test_unpaired_unicode_employer_keys_are_queued_without_encoding_failure(codepoint: int) -> None:
    """JSON-permitted unpaired surrogates stay unresolved and retain safe opaque pointers."""
    key = "https://www.linkedin.com/in/invalid-" + chr(codepoint)
    employers = {PROFILE_A: {"company_id": "co-a", "citations": [citation("employer-a")]}, key: None}
    result = run(source_export(), qualification(current_employers=employers))
    queued = [item for item in result["research_queue"] if item["reason"] == "current_employer_identity_invalid"]
    assert len(queued) == 1
    assert queued[0]["evidence_id"].startswith("current-employer-key-sha256:")
    assert result["status"] == "withheld_current_employer_assignments"
    json.dumps(result, ensure_ascii=False).encode("utf-8")
