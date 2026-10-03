#!/usr/bin/env python3
"""Combine existing private LinkedIn action outputs without provider or database access."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Any, NoReturn

from linkedin_mdp_mcp.combined_action_report import (
    CombinedReportError,
    combine_action_reports,
)
from linkedin_mdp_mcp.google_doc_report import GoogleReportError


class _Parser(argparse.ArgumentParser):
    """Keep malformed arguments on the fixed private diagnostic path."""

    def error(self, message: str) -> NoReturn:
        """Refuse invalid arguments without printing private path text."""
        raise CombinedReportError("arguments_invalid")


def _private_file(path: Path) -> Any:
    """Decode one regular owner-only JSON file without following a symlink."""
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
        raise CombinedReportError("input_permissions_invalid")
    return json.loads(path.read_text(encoding="utf-8"))


def _remove_owned(path: Path, identity: tuple[int, int]) -> None:
    """Remove an invocation-created file only while its device and inode still match."""
    info = path.lstat()
    if (info.st_dev, info.st_ino) == identity:
        path.unlink()


def _write(path: Path, content: str) -> tuple[int, int]:
    """Create a mode-0600 artifact, remove failed output, and return its ownership identity."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    info = os.fstat(descriptor)
    identity = (info.st_dev, info.st_ino)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as output:
            output.write(content)
    except BaseException:
        try:
            _remove_owned(path, identity)
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)
    return identity


def main(argv: list[str] | None = None) -> int:
    """Read two upstream outputs, then create one private JSON and text report."""
    created: list[tuple[Path, tuple[int, int]]] = []
    directory: tuple[Path, tuple[int, int]] | None = None
    try:
        parser = _Parser(description="Build a private combined LinkedIn action report.")
        parser.add_argument("--invitations", type=Path, required=True)
        parser.add_argument("--dm-actions", type=Path, required=True)
        parser.add_argument("--output-dir", type=Path, required=True)
        args = parser.parse_args(argv)
        result = combine_action_reports(_private_file(args.invitations), _private_file(args.dm_actions))
        json_text = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
        json_text.encode("utf-8")
        result["text"].encode("utf-8")
        output_dir = args.output_dir
        if output_dir.exists() or output_dir.is_symlink() or not output_dir.parent.is_dir() or output_dir.parent.is_symlink():
            raise CombinedReportError("output_path_invalid")
        output_dir.mkdir(mode=0o700)
        info = output_dir.lstat()
        directory = (output_dir, (info.st_dev, info.st_ino))
        if stat.S_IMODE(output_dir.stat().st_mode) != 0o700:
            raise CombinedReportError("output_permissions_invalid")
        for name, content in (("combined-action-report.json", json_text), ("combined-action-report.txt", result["text"])):
            path = output_dir / name
            created.append((path, _write(path, content)))
        print("combined action report: written")
        return 0
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError, GoogleReportError):
        for path, identity in created:
            try:
                _remove_owned(path, identity)
            except OSError:
                pass
        if directory is not None:
            path, identity = directory
            try:
                info = path.lstat()
                if (info.st_dev, info.st_ino) == identity:
                    path.rmdir()
            except OSError:
                pass
        print("combined action report: failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
