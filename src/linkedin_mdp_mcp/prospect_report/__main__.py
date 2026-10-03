"""Command-line entry point for the offline prospect report."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .engine import resolve
from .files import ProspectReportError, parse_as_of, read_batch, write_report


def _parser() -> argparse.ArgumentParser:
    """Build the intentionally small file-to-file CLI surface."""
    parser = argparse.ArgumentParser(description="Reconcile prospect evidence into a canonical report")
    parser.add_argument("input", type=Path, help="Input evidence batch JSON")
    parser.add_argument("--as-of", required=True, help="Timezone-aware ISO-8601 report time")
    parser.add_argument("--output", required=True, type=Path, help="Canonical report JSON path")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate input, resolve the report and atomically write the output artifact."""
    args = _parser().parse_args(argv)
    try:
        batch = read_batch(args.input)
        report = resolve(batch, as_of=parse_as_of(args.as_of))
        write_report(report, args.output)
    except ProspectReportError as exc:
        print(f"error: {exc.code}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
