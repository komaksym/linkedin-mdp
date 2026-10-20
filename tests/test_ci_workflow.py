"""Stable CI must run on every PR and main push without path filters or live secrets."""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _read() -> str:
    assert WORKFLOW.is_file(), "missing .github/workflows/ci.yml"
    return WORKFLOW.read_text()


def test_triggers_on_every_pull_request_and_main_push() -> None:
    text = _read()
    assert "pull_request:" in text
    assert "push:" in text
    assert "branches:" in text and "main" in text
    assert "paths:" not in text


def test_stable_job_is_read_only_and_synthetic_only() -> None:
    text = _read()
    assert "stable-ci" in text
    assert "contents: read" in text
    assert "secrets." not in text
    assert "ORION_TOKEN" not in text
    assert "LINKEDIN_ACCESS_TOKEN" not in text


def test_stable_job_runs_real_commands() -> None:
    text = _read()
    assert "uv sync" in text
    assert "python -m pytest" in text
    assert "e2e_doc_report" in text
    assert "--evidence" in text
    assert "uv build" in text
    assert "ruff" in text
    assert "mypy" in text
