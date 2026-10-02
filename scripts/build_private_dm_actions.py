#!/usr/bin/env python3
"""Write a private, offline, read-only LinkedIn DM action report."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

from linkedin_mdp_mcp.dm_actions import (
    _aware_time,
    classify_dm_evidence,
    plan_dm_actions,
)


class _Parser(argparse.ArgumentParser):
    """Keep invalid arguments on a fixed private diagnostic path."""

    def error(self, message: str) -> NoReturn:
        """Raise without printing any argument or path."""
        raise ValueError("arguments_invalid")


def _read(path: Path) -> Any:
    """Read one regular mode-0600 JSON input."""
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
        raise ValueError("input_permissions_invalid")
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, content: str) -> None:
    """Create one new private artifact without replacing an existing file."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as output:
            output.write(content)
    finally:
        os.close(descriptor)


def _markdown(result: dict[str, Any]) -> str:
    """Render action groups and uncertainty inside the private report."""
    lines = ["# Private LinkedIn DM actions", "", "Owner reviews every action and sends manually.", ""]
    for title, key in (("Replies", "reply"), ("Follow-ups", "follow_up"), ("First DMs", "first_dm"), ("Withheld", "withheld")):
        lines.extend((f"## {title}", ""))
        for row in result[key]:
            lines.extend((f"### {row['profile_url']}", "", f"- Reason: {row['reason']}", f"- Prospect ID: {row.get('prospect_id', 'unknown')}", f"- Date added: {row.get('date_added') or 'unknown'}", f"- Date source: {row.get('date_added_source') or 'unknown'}"))
            if "message" in row:
                lines.append(f"- Verified message: {row['message']}")
            if "draft" in row:
                lines.append(f"- Connected on: {row['connected_on']} (calendar day)")
                lines.append(f"- Connection source: {row['connection_source']}")
                lines.append(f"- Saved researched draft: {row['draft']}")
                for citation in row["citations"]:
                    lines.append(f"- Research source: {citation['source_url']} ({citation['fact']})")
            if "source_refs" in row:
                lines.append(f"- Message sources: {', '.join(row['source_refs'])}")
            if "read_state" in row:
                lines.append(f"- Read state: {row['read_state']}")
            lines.append("")
    lines.extend(("## Coverage", "", f"- Changelog scope: {result['coverage']['changelog_scope']}", f"- Upstream freshness: {result['coverage']['upstream_freshness']}", f"- Withholding reasons: {', '.join(result['coverage']['reasons']) or 'none'}", ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Validate private inputs and write fresh mode-0700/0600 local outputs."""
    try:
        parser = _Parser(description="Build a private LinkedIn DM action report.")
        for name in ("source", "changelog", "policy", "research"):
            parser.add_argument(f"--{name}", required=True, type=Path)
        parser.add_argument("--output-dir", required=True, type=Path)
        parser.add_argument("--account-profile-url", required=True)
        parser.add_argument("--now", help="Timezone-aware ISO clock for repeatable offline runs")
        args = parser.parse_args(argv)
        source = _read(args.source)
        changelog = _read(args.changelog)
        policy = _read(args.policy)
        research = _read(args.research)
        if not all(isinstance(item, dict) for item in (source, changelog, policy, research)):
            raise ValueError("input_shape_invalid")
        account_member_urn = source.get("account_member_urn")
        if not isinstance(account_member_urn, str) or re.fullmatch(r"urn:li:person:[A-Za-z0-9_-]+", account_member_urn) is None:
            raise ValueError("source_account_member_urn_invalid")
        now = datetime.fromisoformat(args.now) if args.now else datetime.now().astimezone()
        source_acquired = _aware_time(source.get("collected_at"))
        if source.get("schema_version") != 1 or source_acquired is None or source_acquired > now or now - source_acquired > timedelta(days=1):
            raise ValueError("source_manifest_invalid")
        evidence = classify_dm_evidence(source["snapshots"]["INBOX"], changelog, account_profile_url=args.account_profile_url, account_member_urn=account_member_urn, now=now)
        result = plan_dm_actions(evidence, source["prospects"], source["snapshots"]["CONNECTIONS"], research, policy=policy, now=now)
        output_dir = args.output_dir
        if output_dir.exists() or output_dir.is_symlink() or not output_dir.parent.is_dir() or output_dir.parent.is_symlink():
            raise ValueError("output_path_invalid")
        output_dir.mkdir(mode=0o700)
        if stat.S_IMODE(output_dir.stat().st_mode) != 0o700:
            raise ValueError("output_permissions_invalid")
        _write(output_dir / "private-dm-actions.json", json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        _write(output_dir / "private-dm-actions.md", _markdown(result))
        print("private DM actions: report written")
        return 0
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        print("private DM actions: failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
