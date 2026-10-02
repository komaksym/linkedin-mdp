#!/usr/bin/env python3
"""Publish one prepared private report into an existing restricted Google Doc."""

from __future__ import annotations

import argparse
import asyncio
import stat
from pathlib import Path
from typing import NoReturn

from linkedin_mdp_mcp.google_doc_report import (
    GoogleDocConfig,
    GoogleDocPublisher,
    GoogleReportError,
    validate_report_text,
)


class _Parser(argparse.ArgumentParser):
    """Keep invalid arguments on a fixed private diagnostic path."""

    def error(self, message: str) -> NoReturn:
        """Reject malformed arguments without printing private input names."""
        raise GoogleReportError("arguments_invalid")


async def _publish(path: Path) -> None:
    """Validate a private prepared file before OAuth and publish only its text."""
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
        raise GoogleReportError("report_file_invalid")
    text = validate_report_text(path.read_text(encoding="utf-8"))
    publisher = GoogleDocPublisher(GoogleDocConfig.from_env())
    try:
        await publisher.publish_text(text)
    finally:
        await publisher.aclose()


def main(argv: list[str] | None = None) -> int:
    """Publish prepared text with a fixed success or failure diagnostic."""
    try:
        parser = _Parser(description="Publish a prepared private LinkedIn action report.")
        parser.add_argument("--report", type=Path, required=True)
        args = parser.parse_args(argv)
        asyncio.run(_publish(args.report))
        print("combined action report publication: written")
        return 0
    except (OSError, UnicodeError, ValueError, TypeError, GoogleReportError):
        print("combined action report publication: failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
