"""D476 CLI argv contract test for POST /api/ingestion/extract → extraction_bridge (Chunk 79, D508)."""

import sys
from uuid import uuid4

from src.api.ingestion_routes import _build_extract_bridge_argv
from src.extraction.extraction_bridge import _build_argparser


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


def test_extract_bridge_argv_matches_argparser():
    """Route-constructed argv is accepted by extraction_bridge's argparser."""
    source_id = uuid4()
    argv = _build_extract_bridge_argv(
        source_id=source_id,
        limit=50,
        skip_privileged=True,
    )
    ns = assert_route_spawn_matches_argparser(argv, _build_argparser)
    assert ns.command == "run"
    assert ns.source_id == str(source_id)
    assert ns.limit == 50
    assert ns.skip_privileged is True


def test_extract_bridge_argv_minimal():
    """Minimal argv (no optional flags) is accepted by the argparser."""
    argv = _build_extract_bridge_argv()
    ns = assert_route_spawn_matches_argparser(argv, _build_argparser)
    assert ns.command == "run"
    assert ns.source_id is None
    assert ns.skip_privileged is False
