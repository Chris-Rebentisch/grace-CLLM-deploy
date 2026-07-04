"""Shared logging utilities for CLI batches (D454)."""

import logging


def clamp_http_client_logs(level: int = logging.WARNING) -> None:
    """Set httpcore, httpx, and urllib3 loggers to the supplied level.

    Idempotent — calling multiple times produces the same result.
    Default WARNING allows connection errors (ERROR-level) through
    while silencing per-request DEBUG/INFO noise.
    """
    for name in ("httpcore", "httpx", "urllib3"):
        logging.getLogger(name).setLevel(level)
