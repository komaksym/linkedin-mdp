from __future__ import annotations

from e2e_live import _reference_status_action


def test_reference_404_is_terminal_not_found():
    assert _reference_status_action(404) == "not_found"


def test_reference_retryable_statuses_retry():
    for status in (429, 500, 502, 503, 504):
        assert _reference_status_action(status) == "retry"


def test_reference_auth_and_client_errors_fail():
    for status in (400, 401, 403, 422):
        assert _reference_status_action(status) == "fail"
