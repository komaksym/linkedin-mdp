#!/usr/bin/env python3
"""Publish private first-DM and follow-up reports to separate owner-only Google Docs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn, overload

import httpx

from linkedin_mdp_mcp.dm_doc_report import (
    DmDocReportError,
    PublicationVerdicts,
    QueueVerdict,
    build_dm_doc_bodies,
)
from linkedin_mdp_mcp.google_doc_report import (
    GoogleDocConfig,
    GoogleDocPublisher,
    GoogleReportError,
)

_FIRST_DM_ENV = "GOOGLE_FIRST_DM_REPORT_DOCUMENT_ID"
_FOLLOW_UP_ENV = "GOOGLE_FOLLOW_UP_REPORT_DOCUMENT_ID"
_VERIFICATION_QUEUES = ("first_dm", "reply", "follow_up")
_LOWERCASE_HEX = frozenset("0123456789abcdef")


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


def _read_private_json_bytes(path: Path) -> tuple[Any, bytes]:
    """Read one private JSON input and preserve the bytes used by an exact digest."""
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
        raise ValueError("input_permissions_invalid")
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), raw


def _clock(value: str | None) -> datetime:
    """Accept only a timezone-aware publication clock."""
    parsed = datetime.fromisoformat(value) if value else datetime.now().astimezone()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("clock_timezone_invalid")
    return parsed


def _publication_inputs(
    actions_path: Path | list[str] | None,
    retained_evidence_path: Path | None,
    verification_path: Path | None,
) -> tuple[Path, Path, Path, datetime]:
    """Resolve the canonical path API or the temporary argv compatibility path."""
    if isinstance(actions_path, Path):
        if retained_evidence_path is None or verification_path is None:
            raise ValueError("publication_input_missing")
        return actions_path, retained_evidence_path, verification_path, _clock(None)
    if retained_evidence_path is not None or verification_path is not None:
        raise ValueError("arguments_invalid")

    parser = _Parser(description="Publish verified private DM reports to Google Docs.")
    parser.add_argument("--actions", required=True, type=Path)
    parser.add_argument("--retained-evidence", required=True, type=Path)
    parser.add_argument("--verification", required=True, type=Path)
    parser.add_argument("--now", help="Timezone-aware ISO clock for repeatable offline runs")
    args = parser.parse_args(actions_path)
    return args.actions, args.retained_evidence, args.verification, _clock(args.now)


def _publication_verdicts(value: Any, *, candidate_sha256: str) -> PublicationVerdicts:
    """Parse the exact version-1 verifier shape bound to candidate bytes."""
    if not isinstance(value, dict) or set(value) != {"version", "candidate_sha256", "queues"}:
        raise ValueError("verification_shape_invalid")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("verification_shape_invalid")
    digest = value["candidate_sha256"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in _LOWERCASE_HEX for char in digest)
        or digest != candidate_sha256
    ):
        raise ValueError("verification_candidate_mismatch")
    queues = value["queues"]
    if not isinstance(queues, dict) or set(queues) != set(_VERIFICATION_QUEUES):
        raise ValueError("verification_shape_invalid")
    verdicts: dict[str, QueueVerdict] = {}
    for queue in _VERIFICATION_QUEUES:
        row = queues[queue]
        if not isinstance(row, dict) or set(row) != {"verdict"}:
            raise ValueError("verification_shape_invalid")
        verdict = row["verdict"]
        if not isinstance(verdict, str):
            raise ValueError("verification_verdict_invalid")
        try:
            verdicts[queue] = QueueVerdict(verdict)
        except ValueError as exc:
            raise ValueError("verification_verdict_invalid") from exc
    return PublicationVerdicts(
        first_dm=verdicts["first_dm"],
        reply=verdicts["reply"],
        follow_up=verdicts["follow_up"],
    )


@overload
async def run(
    actions_path: Path,
    retained_evidence_path: Path,
    verification_path: Path,
) -> int: ...


@overload
async def run(
    actions_path: list[str] | None = None,
    retained_evidence_path: None = None,
    verification_path: None = None,
    *,
    http: httpx.AsyncClient | None = None,
) -> int: ...


async def run(
    actions_path: Path | list[str] | None = None,
    retained_evidence_path: Path | None = None,
    verification_path: Path | None = None,
    *,
    http: httpx.AsyncClient | None = None,
) -> int:
    """Validate all private inputs, then publish only destinations whose queue is PASS."""
    try:
        actions_file, retained_file, verification_file, now = _publication_inputs(
            actions_path,
            retained_evidence_path,
            verification_path,
        )
        actions, actions_raw = _read_private_json_bytes(actions_file)
        retained = _read_private_json(retained_file)
        verification = _read_private_json(verification_file)
        verdicts = _publication_verdicts(
            verification,
            candidate_sha256=hashlib.sha256(actions_raw).hexdigest(),
        )
        bodies = build_dm_doc_bodies(actions, retained, now=now)
    except (DmDocReportError, OSError, UnicodeError, ValueError, TypeError, KeyError):
        print("private DM docs: input invalid", file=sys.stderr)
        return 1

    if not verdicts.publishes_first_dm and not verdicts.publishes_follow_up:
        print("private DM docs: no eligible destinations")
        return 0

    try:
        first_config = (
            GoogleDocConfig.from_env(document_id_env=_FIRST_DM_ENV)
            if verdicts.publishes_first_dm
            else None
        )
        follow_config = (
            GoogleDocConfig.from_env(document_id_env=_FOLLOW_UP_ENV)
            if verdicts.publishes_follow_up
            else None
        )
        if (
            first_config is not None
            and follow_config is not None
            and first_config.document_id == follow_config.document_id
        ):
            raise GoogleReportError("destinations must be distinct")
    except (GoogleReportError, OSError):
        print("private DM docs: configuration unavailable", file=sys.stderr)
        return 2

    client = http or httpx.AsyncClient(timeout=30, follow_redirects=False)
    owned = http is None
    first = GoogleDocPublisher(first_config, http=client) if first_config is not None else None
    follow = GoogleDocPublisher(follow_config, http=client) if follow_config is not None else None
    publication_failed = False
    cleanup_failed = False
    try:
        if first is not None:
            await first.prepare()
        if follow is not None:
            await follow.prepare()
        if first is not None:
            await first.publish_text(bodies.first_dm)
        if follow is not None:
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
