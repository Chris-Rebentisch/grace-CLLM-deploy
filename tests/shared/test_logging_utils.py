"""D454 — Tests for ``src.shared._logging_utils.clamp_http_client_logs``."""

from __future__ import annotations

import logging

from src.shared._logging_utils import clamp_http_client_logs


class TestSetsWarningOnAllThreeLoggers:
    """After call, httpcore, httpx, urllib3 all at WARNING."""

    def test_sets_warning_on_all_three_loggers(self) -> None:
        # Reset to DEBUG first to confirm the function changes them
        for name in ("httpcore", "httpx", "urllib3"):
            logging.getLogger(name).setLevel(logging.DEBUG)

        clamp_http_client_logs()

        for name in ("httpcore", "httpx", "urllib3"):
            assert logging.getLogger(name).level == logging.WARNING, (
                f"{name} should be WARNING after clamp"
            )


class TestIdempotentOnDoubleCall:
    """Calling twice produces same state."""

    def test_idempotent_on_double_call(self) -> None:
        clamp_http_client_logs()
        levels_first = {
            name: logging.getLogger(name).level
            for name in ("httpcore", "httpx", "urllib3")
        }

        clamp_http_client_logs()
        levels_second = {
            name: logging.getLogger(name).level
            for name in ("httpcore", "httpx", "urllib3")
        }

        assert levels_first == levels_second


class TestCustomLevelOverride:
    """clamp_http_client_logs(logging.ERROR) sets all three to ERROR."""

    def test_custom_level_override(self) -> None:
        clamp_http_client_logs(logging.ERROR)

        for name in ("httpcore", "httpx", "urllib3"):
            assert logging.getLogger(name).level == logging.ERROR, (
                f"{name} should be ERROR after clamp(ERROR)"
            )

        # Reset to WARNING for test isolation
        clamp_http_client_logs(logging.WARNING)
