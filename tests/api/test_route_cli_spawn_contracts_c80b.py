"""D476 CLI argv contract tests for Chunk 80b bootstrap pipe spawn route.

Validates:
1. Route-constructed argv is accepted by bootstrap_pipe's argparser.
2. Concurrent spawn returns 409.
"""

import sys
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.api.ingestion_routes import _build_bootstrap_argv
from src.ingestion.communications.bootstrap_pipe import _build_argparser


def assert_route_spawn_matches_argparser(
    argv: list[str],
    cli_argparser_factory,
) -> object:
    """Validate that argv round-trips through the CLI argparser."""
    flags = list(argv)
    if flags and flags[0] == sys.executable:
        flags = flags[1:]
    if len(flags) >= 2 and flags[0] == "-m":
        flags = flags[2:]
    parser = cli_argparser_factory()
    return parser.parse_args(flags)


def test_bootstrap_argv_contract():
    """Route-constructed argv is accepted by bootstrap_pipe's argparser."""
    subset_id = uuid4()
    argv = _build_bootstrap_argv(subset_id)
    ns = assert_route_spawn_matches_argparser(argv, _build_argparser)
    assert ns.command == "run"
    assert ns.subset_id == str(subset_id)


def test_bootstrap_409_concurrent():
    """Second concurrent spawn request returns 409."""
    from src.api.ingestion_routes import _IN_FLIGHT_BOOTSTRAP

    subset_id = uuid4()
    lock_key = f"bootstrap:{subset_id}"

    # Simulate an in-flight process
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None  # Still running

    _IN_FLIGHT_BOOTSTRAP[lock_key] = mock_proc

    try:
        from fastapi.testclient import TestClient

        from src.api.main import app

        client = TestClient(app)
        response = client.post(
            "/api/ingestion/bootstrap",
            json={"subset_id": str(subset_id)},
        )
        assert response.status_code == 409
        assert "Bootstrap already in progress" in response.json()["detail"]
    finally:
        _IN_FLIGHT_BOOTSTRAP.pop(lock_key, None)
