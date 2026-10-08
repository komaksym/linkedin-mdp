#!/usr/bin/env python3
"""Run one dry-by-default actual-action sync with fixed private diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
from dataclasses import asdict
from pathlib import Path
from typing import NoReturn

from linkedin_mdp_mcp.actual_action_sync import (
    ActualActionSyncSummary,
    ActualSyncError,
    sync_actual_actions,
)
from linkedin_mdp_mcp.client import LinkedInMDPClient
from linkedin_mdp_mcp.supabase_client import SupabaseClient


class _Parser(argparse.ArgumentParser):
    """Keep private invocation arguments out of parser diagnostics."""

    def error(self, message: str) -> NoReturn:
        """Refuse malformed arguments with one fixed code."""
        raise ActualSyncError("arguments_invalid")


def _new_output_path(path: Path | None) -> None:
    """Require an unused directory path before acquiring any external source."""
    if path is None:
        return
    if path.exists() or path.is_symlink() or not path.parent.is_dir() or path.parent.is_symlink():
        raise ActualSyncError("output_path_invalid")


def _write_manifest(path: Path, summary: ActualActionSyncSummary) -> None:
    """Create a new owner-only aggregate result without member-level evidence."""
    _new_output_path(path)
    content = json.dumps(asdict(summary), indent=2, sort_keys=True) + "\n"
    path.mkdir(mode=0o700)
    file = path / "actual-action-sync.json"
    created_file = False
    try:
        if stat.S_IMODE(path.stat().st_mode) != 0o700:
            raise ActualSyncError("output_permissions_invalid")
        descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        created_file = True
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as output:
                output.write(content)
        finally:
            os.close(descriptor)
    except Exception:
        if created_file:
            try:
                file.unlink()
            except OSError:
                pass
        try:
            path.rmdir()
        except OSError:
            pass
        raise


async def run(apply: bool, account_profile_url: str, account_member_urn: str, output_dir: Path | None = None) -> int:
    """Acquire, plan, optionally apply, and print only a fixed outcome token."""
    linkedin = None
    supabase = None
    failure: str | None = None
    summary = None
    try:
        _new_output_path(output_dir)
        linkedin = LinkedInMDPClient.from_env()
        supabase = SupabaseClient.from_env()
        summary = await sync_actual_actions(linkedin, supabase, account_profile_url=account_profile_url, account_member_urn=account_member_urn, apply=apply)
        if output_dir is not None:
            _write_manifest(output_dir, summary)
    except ActualSyncError as exc:
        failure = "PERSISTENCE_OUTCOME_UNKNOWN" if exc.code == "persistence_outcome_unknown" else "APPLIED_DIAGNOSTIC_FAILED" if summary is not None and summary.status == "applied" else "FAILED"
    except Exception:  # noqa: BLE001
        failure = "APPLIED_DIAGNOSTIC_FAILED" if summary is not None and summary.status == "applied" else "FAILED"
    for client in (supabase, linkedin):
        if client is None:
            continue
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            if failure != "PERSISTENCE_OUTCOME_UNKNOWN":
                failure = "APPLIED_DIAGNOSTIC_FAILED" if summary is not None and summary.status == "applied" else "FAILED"
    if failure is not None:
        print(failure)
        return 1
    if summary is None:
        print("FAILED")
        return 1
    print("APPLIED" if summary.status == "applied" else "DRY_RUN_COMPLETE")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse explicit account identity and opt-in persistence arguments."""
    try:
        parser = _Parser(description="Sync only verified actual LinkedIn actions.")
        parser.add_argument("--account-profile-url", required=True)
        parser.add_argument("--account-member-urn", required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--output-dir", type=Path)
        args = parser.parse_args(argv)
        return asyncio.run(run(args.apply, args.account_profile_url, args.account_member_urn, args.output_dir))
    except (ActualSyncError, OSError, ValueError):
        print("FAILED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
