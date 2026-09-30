"""JSON boundary and atomic artifact I/O for prospect reconciliation."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from pydantic import ValidationError

from .model import Batch, Report, require_aware


class ProspectReportError(Exception):
    """Safe boundary error carrying only a stable non-sensitive code."""

    def __init__(self, code: str) -> None:
        """Store a stable code without embedding raw input in the exception message."""
        super().__init__(code)
        self.code = code


def parse_as_of(value: str) -> datetime:
    """Parse an ISO-8601 report clock and require an explicit timezone."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return require_aware(parsed)
    except (TypeError, ValueError) as exc:
        raise ProspectReportError("invalid_as_of") from exc


def read_batch(path: Path) -> Batch:
    """Read and strictly validate one input file without exposing raw values on failure."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return Batch.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ProspectReportError("invalid_input") from exc


def canonical_report_bytes(report: Report) -> bytes:
    """Encode a report with stable key/list ordering and a trailing newline."""
    text = json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False)
    return (text + "\n").encode("utf-8")


def write_report(report: Report, path: Path) -> None:
    """Atomically replace a report so failures never leave a partial artifact."""
    temp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(canonical_report_bytes(report))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except OSError as exc:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise ProspectReportError("output_write_failed") from exc
