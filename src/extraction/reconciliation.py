"""Reconciliation CLI mirror (Chunk 34, D256, D246).

Wraps ``src.extraction.provenance.reconciliation_check(client, session)``
directly and prints the same ``{promoted, warnings, checked}`` payload
the API surface returns. Out-of-process per D246 — no scheduler.

B1 resolution: NO filter parameters (``--document-id``, ``--since``,
``--batch-size`` are intentionally absent).

Usage::

    python -m src.extraction.reconciliation [--json] [--verbose]

Exit codes:
    0 — call completed (regardless of warnings count).
    2 — unrecoverable error during invocation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.extraction import provenance
from src.graph.arcade_client import ArcadeClient
from src.graph.config import ArcadeConfig

# Pinned response key set; the API surface (extraction_routes) and this CLI
# must agree on these (test_reconciliation_payload_parity_with_cli).
RESPONSE_KEYS = ("promoted", "warnings", "checked")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grace-reconciliation",
        description=(
            "Idempotent dual-write reconciliation between PostgreSQL "
            "extraction_events and ArcadeDB Extraction_Event vertices. "
            "Wraps src.extraction.provenance.reconciliation_check (D256). "
            "No filter parameters (B1 resolution)."
        ),
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Print machine-readable JSON to stdout (default).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging.",
    )
    return parser


def _make_session_factory():
    """Build a SQLAlchemy sessionmaker from GraceSettings.database_url."""
    from src.shared.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


async def _run(args: argparse.Namespace) -> int:
    arcade_client = ArcadeClient(config=ArcadeConfig())
    session_factory = _make_session_factory()
    session: Session = session_factory()
    try:
        result = await provenance.reconciliation_check(arcade_client, session)
    finally:
        session.close()

    payload = {key: int(result.get(key, 0)) for key in RESPONSE_KEYS}
    print(json.dumps(payload))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return asyncio.run(_run(args))
    except Exception as exc:  # noqa: BLE001
        logging.exception("reconciliation_unrecoverable_error: %s", exc)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
