"""Exercise time-scoped anonymous DM uncertainty through the real classifier and planner."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from e2e_private_dm_actions import activity, inbox_row, report

ROOT = Path(__file__).resolve().parents[1]


def anonymous_row(*, hours: int) -> dict[str, Any]:
    """Build a timestamped INBOX row whose participant identity is unavailable."""
    row = deepcopy(inbox_row(hours=hours, content="Synthetic anonymous evidence"))
    row["CONVERSATION ID"] = f"anonymous-thread-{hours}"
    row.pop("SENDER PROFILE URL")
    row["RECIPIENT PROFILE URLS"] = []
    return row


def run_matrix() -> dict[str, str]:
    """Prove anonymous evidence blocks only actions whose decision window it can change."""
    verdicts: dict[str, str] = {}

    def check(name: str, condition: bool) -> None:
        """Record one deterministic behavior-level assertion."""
        if not condition:
            raise AssertionError(name)
        verdicts[name] = "passed"

    outbound = inbox_row(hours=73)
    old = anonymous_row(hours=100)
    old_result = report([outbound, old], [activity(outbound)])
    check("older_anonymous_observation_allows_due_followup", len(old_result["follow_up"]) == 1)

    recent = anonymous_row(hours=1)
    recent_result = report([outbound, recent], [activity(outbound)])
    check(
        "newer_anonymous_observation_blocks_due_followup",
        not recent_result["follow_up"]
        and recent_result["withheld"]
        and recent_result["withheld"][0]["reason"] == "message_history_ambiguous_or_action_not_due",
    )

    incoming = inbox_row(inbound=True, hours=1)
    old_reply_result = report([incoming, old], [activity(incoming, inbound=True)])
    check("older_anonymous_observation_allows_verified_reply", len(old_reply_result["reply"]) == 1)

    newest = anonymous_row(hours=0)
    recent_reply_result = report([incoming, newest], [activity(incoming, inbound=True)])
    check(
        "newer_anonymous_observation_blocks_verified_reply",
        not recent_reply_result["reply"]
        and recent_reply_result["withheld"]
        and recent_reply_result["withheld"][0]["reason"] == "message_history_ambiguous_or_action_not_due",
    )

    first_result = report([old], [])
    check(
        "anonymous_observation_still_blocks_first_dm",
        not first_result["first_dm"]
        and first_result["withheld"]
        and first_result["withheld"][0]["reason"] == "message_history_ambiguous_or_action_not_due",
    )

    timeless = anonymous_row(hours=100)
    timeless.pop("DATE")
    timeless_result = report([outbound, timeless], [activity(outbound)])
    check(
        "anonymous_observation_without_time_blocks_due_followup",
        not timeless_result["follow_up"]
        and timeless_result["withheld"]
        and timeless_result["withheld"][0]["reason"] == "message_history_ambiguous_or_action_not_due",
    )
    return verdicts


def main(path: Path | None = None) -> None:
    """Write a repeatable verdict artifact and fail loudly on any regression."""
    artifact = path or ROOT / "artifacts" / "dm-uncertainty-scope-e2e-evidence.json"
    artifact.write_text(
        json.dumps({"run_status": "failed", "scenario_count": 0, "verdicts": {}}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    verdicts = run_matrix()
    artifact.write_text(
        json.dumps({"run_status": "passed", "scenario_count": len(verdicts), "verdicts": verdicts}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"DM uncertainty scope: {len(verdicts)} synthetic scenarios passed")


if __name__ == "__main__":
    main()
