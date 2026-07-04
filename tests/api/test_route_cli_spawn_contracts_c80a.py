"""D476 CLI argv contract test for POST /api/ingestion/reconstruct-threads → thread_reconstructor (Chunk 80a, D513)."""

import sys
from uuid import uuid4

from src.api.ingestion_routes import _build_reconstruct_threads_argv
from src.ingestion.communications.thread_reconstructor import _build_argparser


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


def test_reconstruct_threads_argv_matches_argparser():
    """Route-constructed argv is accepted by thread_reconstructor's argparser."""
    source_id = uuid4()
    argv = _build_reconstruct_threads_argv(
        source_id=source_id,
        limit=100,
        reprocess=True,
    )
    ns = assert_route_spawn_matches_argparser(argv, _build_argparser)
    assert ns.command == "run"
    assert ns.source_id == str(source_id)
    assert ns.limit == 100
    assert ns.reprocess is True


def test_reconstruct_threads_argv_minimal():
    """Minimal argv (no optional flags) is accepted by the argparser."""
    argv = _build_reconstruct_threads_argv()
    ns = assert_route_spawn_matches_argparser(argv, _build_argparser)
    assert ns.command == "run"
    assert ns.source_id is None
    assert ns.reprocess is False
