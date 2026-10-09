#!/usr/bin/env python3
"""Publish private first-DM and follow-up reports to separate owner-only Google Docs."""

from __future__ import annotations

import argparse
import asyncio
import json
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn, overload

import httpx

from linkedin_mdp_mcp.dm_doc_report import DmDocReportError, build_dm_doc_bodies
from linkedin_mdp_mcp.google_doc_report import (
    GoogleDocConfig,
    GoogleDocPublisher,
    GoogleReportError,
)

_FIRST_DM_ENV = "GOOGLE_FIRST_DM_REPORT_DOCUMENT_ID"
_FOLLOW_UP_ENV = "GOOGLE_FOLLOW_UP_REPORT_DOCUMENT_ID"


class _Parser(argparse.ArgumentParser):
    """Keep invalid arguments on a fixed private diagnostic path."""

    def error(self, message: str) -> NoReturn:
        raise ValueError("arguments_invalid")


def _read_private_json(path: Path) -> Any:
    """Read one regular private JSON input."""
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
        raise ValueError("input_permissions_invalid")
    return json.loads(path.read_text(encoding="utf-8"))


def _clock(value: str | None) -> datetime:
    """Accept only a timezone-aware publication clock."""
    parsed = datetime.fromisoformat(value) if value else datetime.now().astimezone()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("clock_timezone_invalid")
    return parsed


def _publication_inputs(
    actions_path: Path | list[str] | None,
    retained_evidence_path: Path | None,
) -> tuple[Path, Path, datetime]:
    """Resolve the canonical path API or the temporary argv compatibility path."""
    if isinstance(actions_path, Path):
        if retained_evidence_path is None:
            raise ValueError("retained_evidence_missing")
        return actions_path, retained_evidence_path, _clock(None)
    if retained_evidence_path is not None:
        raise ValueError("arguments_invalid")

    parser = _Parser(description="Publish the private DM reports to Google Docs.")
    parser.add_argument("--actions", required=True, type=Path)
    parser.add_argument("--retained-evidence", required=True, type=Path)
    parser.add_argument("--now", help="Timezone-aware ISO clock for repeatable offline runs")
    args = parser.parse_args(actions_path)
    return args.actions, args.retained_evidence, _clock(args.now)


@overload
async def run(
    actions_path: Path,
    retained_evidence_path: Path,
) -> int: ...


@overload
async def run(
    actions_path: list[str] | None = None,
    retained_evidence_path: None = None,
    *,
    http: httpx.AsyncClient | None = None,
) -> int: ...


async def run(
    actions_path: Path | list[str] | None = None,
    retained_evidence_path: Path | None = None,
    *,
    http: httpx.AsyncClient | None = None,
) -> int:
    """Validate both private inputs and both destinations before the first mutation."""
    try:
        actions_file, retained_file, now = _publication_inputs(actions_path, retained_evidence_path)
        actions = _read_private_json(actions_file)
        retained = _read_private_json(retained_file)
        bodies = build_dm_doc_bodies(actions, retained, now=now)
    except (DmDocReportError, OSError, UnicodeError, ValueError, TypeError, KeyError):
        print("private DM docs: input invalid", file=sys.stderr)
        return 1

    try:
        first_config = GoogleDocConfig.from_env(document_id_env=_FIRST_DM_ENV)
        follow_config = GoogleDocConfig.from_env(document_id_env=_FOLLOW_UP_ENV)
        if first_config.document_id == follow_config.document_id:
            raise GoogleReportError("destinations must be distinct")
    except (GoogleReportError, OSError):
        print("private DM docs: configuration unavailable", file=sys.stderr)
        return 2

    client = http or httpx.AsyncClient(timeout=30, follow_redirects=False)
    owned = http is None
    first = GoogleDocPublisher(first_config, http=client)
    follow = GoogleDocPublisher(follow_config, http=client)
    publication_failed = False
    cleanup_failed = False
    try:
        await first.prepare()
        await follow.prepare()
        await first.publish_text(bodies.first_dm)
        await follow.publish_text(bodies.follow_up)
    except GoogleReportError:
        publication_failed = True
    finally:
        if owned:
            try:
                await client.aclose()
            except Exception:
                cleanup_failed = True

    if publication_failed:
        print("private DM docs: Google publication unavailable", file=sys.stderr)
        return 2
    if cleanup_failed:
        print("private DM docs: cleanup unavailable", file=sys.stderr)
        return 2
    print("private DM docs: published")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the asynchronous publication command and exit with its status only."""
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
