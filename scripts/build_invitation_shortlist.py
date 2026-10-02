#!/usr/bin/env python3
"""Build a private invitation shortlist report and recommendation event plan."""

from __future__ import annotations

import argparse
import json
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from linkedin_mdp_mcp.invitation_shortlist import (
    ShortlistInputError,
    _make_markdown,
    build_invitation_shortlist,
)


class _Parser(argparse.ArgumentParser):
    """Keep malformed command invocations on the fixed-output diagnostic path."""

    def error(self, message: str) -> NoReturn:
        """Raise a fixed safe error instead of printing arguments or environment details."""
        raise ShortlistInputError("arguments_invalid")


def _private_json(path: Path) -> Any:
    """Read one regular private JSON file without printing its path or contents."""
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ShortlistInputError("input_file_permissions_invalid")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ShortlistInputError("input_json_invalid") from exc


def _write_new_private(path: Path, content: str) -> None:
    """Create one new mode-0600 artifact without replacing an existing file."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as output:
            output.write(content)
    finally:
        os.close(descriptor)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    """Parse explicit private input, output, and lifetime-cap policy paths."""
    parser = _Parser(description="Build a private LinkedIn invitation shortlist.")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--qualification", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--cap-scope", required=True, choices=("all_history",))
    parser.add_argument("--now", help="Optional timezone-aware ISO clock for repeatable runs.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Validate private inputs, write fresh mode-0600 outputs, and print a fixed status."""
    try:
        args = _arguments(argv)
        source = _private_json(args.source)
        qualification = _private_json(args.qualification)
        now = datetime.fromisoformat(args.now) if args.now else None
        result = build_invitation_shortlist(source, qualification, cap_scope=args.cap_scope, now=now)
        output_dir = args.output_dir
        if output_dir.exists() or output_dir.is_symlink():
            raise ShortlistInputError("output_directory_must_be_new")
        parent = output_dir.parent
        if not parent.is_dir() or parent.is_symlink():
            raise ShortlistInputError("output_parent_invalid")
        output_dir.mkdir(mode=0o700)
        if stat.S_IMODE(output_dir.stat().st_mode) & 0o077:
            raise ShortlistInputError("output_directory_permissions_invalid")
        _write_new_private(output_dir / "invitation-shortlist.json", json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        _write_new_private(output_dir / "invitation-shortlist.md", _make_markdown(result))
        status = "withheld report written" if result["status"] != "ready" else "report written"
        print(f"invitation shortlist: {status}")
        return 0
    except (OSError, ValueError, TypeError, KeyError, ShortlistInputError):
        print("invitation shortlist: failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
