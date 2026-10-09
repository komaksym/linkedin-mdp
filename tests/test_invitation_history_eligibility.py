"""Prove invitation history eligibility without live credentials."""

from datetime import datetime, timezone
from pathlib import Path

from linkedin_mdp_mcp.invitation_sync import plan_invitation_events

PROFILE = "https://www.linkedin.com/in/example-one"
OBSERVED = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def make_row(url=PROFILE, sent_at="9/17/26, 5:16 AM", direction="OUTGOING"):
    return {"Direction": direction, "inviteeProfileUrl": url, "Sent At": sent_at, "Message": "synthetic"}


def test_history_key_format_and_lifecycle():
    prospects = {PROFILE: "p1"}
    plan = plan_invitation_events([make_row()], prospects, observed_at=OBSERVED)
    assert plan.unique_matched == 1
    event = plan.events[0]
    assert event["event_type"] == "LINKEDIN_INVITATION_HISTORY_FOUND"
    assert event["external_key"].startswith("invitation-history:https://www.linkedin.com/in/example-one:")
    assert event["payload"]["observed_via"] == "INVITATIONS"
    assert event["payload"]["lifecycle_state"] == "unknown"
    assert event["payload"]["canonical_url"] == PROFILE
    assert "PENDING" not in event["payload"]["lifecycle_state"]


def test_history_dedupes_reruns_and_ignores_inbound():
    prospects = {PROFILE: "p1"}
    rows = [make_row(), make_row(), make_row(direction="INCOMING")]
    plan = plan_invitation_events(rows, prospects, observed_at=OBSERVED)
    assert plan.matched_rows == 2
    assert plan.inbound_rows == 1
    assert plan.unique_matched == 1


def test_history_skips_malformed_and_unmatched_without_creating_prospects():
    prospects = {PROFILE: "p1"}
    rows = [make_row(url="https://www.linkedin.com/in/unknown"), make_row(sent_at="bad time"), make_row(sent_at=None)]
    plan = plan_invitation_events(rows, prospects, observed_at=OBSERVED)
    assert plan.unmatched_rows == 1
    assert plan.malformed_rows == 2
    assert plan.unique_matched == 0
    assert plan.events == []


def test_outreach_state_migration_recognizes_history():
    root = Path(__file__).resolve().parents[1]
    migrations = sorted((root / "supabase" / "migrations").glob("*.sql"))
    assert migrations
    history_sql = ""
    for path in migrations:
        text = path.read_text(encoding="utf-8")
        if "LINKEDIN_INVITATION_HISTORY_FOUND" in text and "outreach_state" in text:
            history_sql = text
            break
    assert history_sql
    assert "LINKEDIN_INVITE_SENT" in history_sql
    assert "invite_exists" in history_sql
    assert "invite_sent_at" in history_sql
    assert "timestamp_semantics" in history_sql
    assert "observed_at" in history_sql
    assert "INVITE_SENT_NOT_CONNECTED" in history_sql
    assert "PENDING" not in history_sql
