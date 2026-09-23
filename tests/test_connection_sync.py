from __future__ import annotations

from datetime import datetime, timezone

from linkedin_mdp_mcp.connection_sync import (
    normalize_linkedin_url,
    plan_connection_events,
)


def test_reconciliation_plan_matches_existing_prospects_and_preserves_provider_evidence():
    observed_at = datetime(2026, 9, 24, 0, 30, tzinfo=timezone.utc)
    rows = [
        {
            "First Name": "Ada",
            "Last Name": "Lovelace",
            "URL": " http://uk.linkedin.com/in/ada-lovelace/?trk=abc#bio ",
            "Email Address": "",
            "Company": "Analytical Engines",
            "Position": "Engineer",
            "Connected On": "2026-09-20",
        },
        {
            "First Name": "Unknown",
            "Last Name": "Member",
            "URL": "https://www.linkedin.com/in/not-in-crm",
            "Email Address": "",
            "Company": "",
            "Position": "",
            "Connected On": "2026-09-21",
        },
    ]
    prospects_by_key = {
        "https://www.linkedin.com/in/ada-lovelace": "prospect-1",
    }

    plan = plan_connection_events(rows, prospects_by_key, observed_at=observed_at)

    assert plan.fetched == 2
    assert plan.matched == 1
    assert plan.unmatched == 1
    assert plan.events == [
        {
            "prospect_id": "prospect-1",
            "source": "LINKEDIN_MDP",
            "event_type": "LINKEDIN_CONNECTION_FOUND",
            "external_key": "connection:https://www.linkedin.com/in/ada-lovelace",
            "occurred_at": "2026-09-20T00:00:00+00:00",
            "payload": {
                "raw": rows[0],
                "timestamp_semantics": "event_date_day_precision",
            },
        }
    ]


def test_bad_connection_date_uses_observation_time_without_inventing_precision():
    observed_at = datetime(2026, 9, 24, 0, 30, 12, tzinfo=timezone.utc)
    row = {
        "URL": "https://linkedin.com/in/ada-lovelace/",
        "Connected On": "not-a-date",
    }

    plan = plan_connection_events(
        [row],
        {"https://www.linkedin.com/in/ada-lovelace": "prospect-1"},
        observed_at=observed_at,
    )

    assert plan.events[0]["occurred_at"] == "2026-09-24T00:30:12+00:00"
    assert plan.events[0]["payload"]["timestamp_semantics"] == "observed_at"


def test_missing_or_non_linkedin_url_is_unmatched():
    observed_at = datetime(2026, 9, 24, tzinfo=timezone.utc)

    plan = plan_connection_events(
        [
            {"Connected On": "2026-09-20"},
            {"URL": "https://example.com/in/not-linkedin", "Connected On": "2026-09-20"},
        ],
        {},
        observed_at=observed_at,
    )

    assert plan.fetched == 2
    assert plan.matched == 0
    assert plan.unmatched == 2
    assert plan.events == []


def test_python_normalizer_matches_database_identity_semantics():
    assert (
        normalize_linkedin_url(" HTTP://DE.LINKEDIN.COM/in/Ada/?x=1#bio ")
        == "https://www.linkedin.com/in/Ada"
    )
    assert (
        normalize_linkedin_url("https://www.linkedin.com/in/ada///")
        == "https://www.linkedin.com/in/ada"
    )
    assert normalize_linkedin_url("") is None
    assert normalize_linkedin_url("https://example.com/in/ada") is None
