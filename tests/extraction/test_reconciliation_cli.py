"""Reconciliation CLI tests (Chunk 34, D256).

Exercises ``src.extraction.reconciliation.main`` directly with patched
``provenance.reconciliation_check`` and a stubbed session factory so the
tests run without a real PostgreSQL session or ArcadeDB client.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.extraction import reconciliation as cli_module


def test_cli_argparse_smoke(capsys):
    """``main`` returns 0 and prints JSON with the pinned RESPONSE_KEYS."""
    fake_result = {"promoted": 4, "warnings": 1, "checked": 5}

    fake_session = MagicMock()
    fake_session_factory = MagicMock(return_value=fake_session)

    with patch.object(
        cli_module, "_make_session_factory", return_value=fake_session_factory
    ), patch.object(cli_module, "ArcadeClient"), patch.object(
        cli_module.provenance,
        "reconciliation_check",
        AsyncMock(return_value=fake_result),
    ):
        rc = cli_module.main(["--json"])

    assert rc == 0
    captured = capsys.readouterr()
    import json as _json

    payload = _json.loads(captured.out.strip().splitlines()[-1])
    assert set(payload.keys()) == set(cli_module.RESPONSE_KEYS)
    assert payload == fake_result


def test_cli_exit_code_on_unhandled_exception():
    """Unhandled exception during the run yields exit code 2 (D256/D246)."""
    fake_session_factory = MagicMock(side_effect=RuntimeError("db down"))
    with patch.object(
        cli_module, "_make_session_factory", return_value=fake_session_factory
    ), patch.object(cli_module, "ArcadeClient"), patch.object(
        cli_module.provenance,
        "reconciliation_check",
        AsyncMock(side_effect=RuntimeError("blew up")),
    ):
        rc = cli_module.main([])
    assert rc == 2
