"""ArcadeDB connection configuration."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.shared.config import GraceSettings


def _default_database() -> str:
    # F-022 (validation-run ledger, 2026-07-02): a bare ``ArcadeConfig()`` hardcoded
    # database="grace", silently ignoring ARCADE_DATABASE — every call site that
    # skipped ``get_arcade_client()`` (retriage.py:140, eval_checkpoint.py:328)
    # therefore queried the LIVE graph even inside a grace_test sandbox run
    # (observed live: retriage Tier-2 lookups logged database=grace with
    # ARCADE_DATABASE=grace_test exported — a sandbox-isolation breach, reads
    # only, tripwire verified clean). Same defect family as C1 defect #7 / D538,
    # fixed here at the source so the whole bare-constructor class is safe.
    # Behavior is unchanged when ARCADE_DATABASE is unset (default "grace").
    return os.environ.get("ARCADE_DATABASE", "grace").strip() or "grace"


class ArcadeConfig(BaseModel):
    """ArcadeDB connection configuration."""

    host: str = Field(default="localhost", description="ArcadeDB server host")
    port: int = Field(default=2480, description="ArcadeDB HTTP API port")
    username: str = Field(default="root", description="ArcadeDB username")
    password: str = Field(default="gracedev", description="ArcadeDB root password")
    database: str = Field(default_factory=_default_database, description="ArcadeDB database name (honors ARCADE_DATABASE; F-022)")
    timeout: int = Field(default=30, description="ArcadeDB request timeout in seconds")

    @property
    def base_url(self) -> str:
        """Build the ArcadeDB REST API base URL."""
        return f"http://{self.host}:{self.port}"

    @classmethod
    def from_settings(cls, settings: GraceSettings) -> ArcadeConfig:
        """Create ArcadeConfig from GraceSettings."""
        return cls(
            host=settings.arcade_host,
            port=settings.arcade_port,
            username=settings.arcade_username,
            password=settings.arcade_password,
            database=settings.arcade_database,
            timeout=settings.arcade_timeout,
        )
