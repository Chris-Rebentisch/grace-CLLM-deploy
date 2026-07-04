"""Thin httpx client for ArcadeDB REST API."""

from __future__ import annotations

import time

import httpx
import structlog

from src.graph.config import ArcadeConfig

logger = structlog.get_logger()


class ArcadeDBError(Exception):
    """ArcadeDB server returned an error."""

    def __init__(self, status_code: int, detail: str, query: str = ""):
        self.status_code = status_code
        self.detail = detail
        self.query = query
        super().__init__(f"ArcadeDB error {status_code}: {detail}")


def get_arcade_client() -> "ArcadeClient":
    """Factory: build an ArcadeClient from the current process settings."""
    from src.shared.config import get_settings

    settings = get_settings()
    config = ArcadeConfig.from_settings(settings)
    return ArcadeClient(config=config)


def _default_arcade_config() -> ArcadeConfig:
    """Resolve the default ArcadeConfig from process settings/env.

    C1 defect #7 capture-the-why: a bare ``ArcadeClient()`` used the raw
    ``ArcadeConfig()`` field defaults (``database="grace"``), silently ignoring
    ``ARCADE_DATABASE`` — so harnesses pointed at a ``grace_test`` sandbox hit
    the live GOLD graph. The no-arg constructor now resolves through
    ``GraceSettings`` (env vars + ``.env``), same as ``get_arcade_client()``.
    Behavior is unchanged when nothing is configured (default stays ``grace``);
    if settings cannot load (e.g. ``DATABASE_URL`` unset in a bare context),
    fall back to the plain defaults.
    """
    try:
        from src.shared.config import get_settings

        return ArcadeConfig.from_settings(get_settings())
    except Exception as exc:  # noqa: BLE001 — settings unavailable; keep old defaults
        logger.warning("arcade.default_config_fallback", error=str(exc))
        return ArcadeConfig()


class ArcadeClient:
    """Thin httpx client for ArcadeDB REST API.

    Connection pooling: a single ``httpx.AsyncClient`` is created lazily on
    first use and reused across all subsequent calls on this instance (keep-alive
    + bounded pool), instead of opening a brand-new client/connection per query.
    The prior per-call pattern produced thousands of short-lived connections
    during a full run, piling up server-side session/auth state in ArcadeDB and
    adding needless fd/thread churn. The pooled client is created within whatever
    event loop first uses it and reused for that instance's lifetime; instances
    are normally scoped to one event loop. Call ``aclose()`` to release it (a
    finished/closed client is transparently re-created on next use).
    """

    def __init__(self, config: ArcadeConfig | None = None):
        """Initialize with ArcadeConfig, or resolve defaults from settings/env
        (honors ``ARCADE_DATABASE`` — C1 defect #7; see ``_default_arcade_config``)."""
        self.config = config or _default_arcade_config()
        self.base_url = self.config.base_url
        self._auth = (self.config.username, self.config.password)
        self._timeout = self.config.timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Return the shared pooled client, (re)creating it if absent/closed."""
        client = self._client
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                base_url=self.base_url,
                auth=self._auth,
                timeout=self._timeout,
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=30.0,
                ),
            )
            self._client = client
        return client

    async def aclose(self) -> None:
        """Close the pooled client and its connections, if open."""
        client = self._client
        self._client = None
        if client is not None and not client.is_closed:
            await client.aclose()

    def reset_pool(self) -> None:
        """Drop the pooled httpx client reference WITHOUT awaiting close (sync).

        D541: the pooled client is bound to the event loop that first used it. Sync
        CLI helpers that call ``asyncio.run`` repeatedly (e.g. thread-reconstruction
        per-thread loads + supersession writes) leave the client bound to a closed
        loop; reusing it raises ``RuntimeError: Event loop is closed``. Call this
        before a fresh ``asyncio.run`` boundary so ``_get_client`` recreates the
        client in the new loop. The orphaned client's sockets are reaped by GC; this
        is a low-frequency batch path, not the hot request path.
        """
        self._client = None

    async def health_check(self) -> dict:
        """GET /api/v1/server — returns server info or raises ConnectionError."""
        client = self._get_client()
        try:
            resp = await client.get("/api/v1/server")
            if resp.status_code >= 400:
                detail = resp.text
                logger.error(
                    "arcade.health_check.error",
                    status_code=resp.status_code,
                    detail=detail,
                )
                raise ArcadeDBError(resp.status_code, detail)
            result = resp.json()
            logger.info("arcade.health_check", status="ok")
            return result
        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"ArcadeDB not reachable at {self.base_url}"
            ) from exc

    async def server_ready(self) -> bool:
        """Returns True if server responds to health check, False otherwise."""
        try:
            await self.health_check()
            return True
        except (ConnectionError, ArcadeDBError):
            return False

    async def create_database(self, name: str) -> dict:
        """POST /api/v1/server — create a new database."""
        client = self._get_client()
        try:
            resp = await client.post(
                "/api/v1/server",
                json={"command": f"create database {name}"},
            )
            if resp.status_code >= 400:
                detail = resp.text
                logger.error(
                    "arcade.create_database.error",
                    database=name,
                    status_code=resp.status_code,
                    detail=detail,
                )
                raise ArcadeDBError(resp.status_code, detail)
            result = resp.json()
            logger.info("arcade.create_database", database=name)
            return result
        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"ArcadeDB not reachable at {self.base_url}"
            ) from exc

    async def database_exists(self, name: str) -> bool:
        """POST /api/v1/server — check if database exists."""
        client = self._get_client()
        try:
            resp = await client.post(
                "/api/v1/server",
                json={"command": "list databases"},
            )
            if resp.status_code >= 400:
                detail = resp.text
                logger.error(
                    "arcade.database_exists.error",
                    status_code=resp.status_code,
                    detail=detail,
                )
                raise ArcadeDBError(resp.status_code, detail)
            result = resp.json()
            databases = result.get("result", [])
            return name in databases
        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"ArcadeDB not reachable at {self.base_url}"
            ) from exc

    async def ensure_database(self, name: str) -> None:
        """Create database if it doesn't exist. Idempotent."""
        if not await self.database_exists(name):
            await self.create_database(name)
            logger.info("arcade.ensure_database", database=name, action="created")
        else:
            logger.info("arcade.ensure_database", database=name, action="exists")

    async def execute_query(
        self, language: str, command: str, database: str | None = None,
        params: dict | None = None,
    ) -> dict:
        """Low-level query execution against a specific database.

        Both execute_sql and execute_cypher delegate here.
        """
        db = database or self.config.database
        start = time.monotonic()
        client = self._get_client()
        try:
            body = {
                "language": language,
                "command": command,
                "serializer": "record",
            }
            if params:
                body["params"] = params
            resp = await client.post(
                f"/api/v1/command/{db}",
                json=body,
            )
            elapsed_ms = round((time.monotonic() - start) * 1000, 1)
            if resp.status_code >= 400:
                detail = resp.text
                logger.error(
                    "arcade.error",
                    language=language,
                    database=db,
                    status_code=resp.status_code,
                    detail=detail,
                )
                raise ArcadeDBError(resp.status_code, detail, query=command)
            result = resp.json()
            logger.info(
                "arcade.query",
                language=language,
                database=db,
                duration_ms=elapsed_ms,
            )
            return result
        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"ArcadeDB not reachable at {self.base_url}"
            ) from exc
        except httpx.TimeoutException as exc:
            logger.warning(
                "arcade.timeout",
                language=language,
                database=db,
                command=command,
            )
            raise TimeoutError(
                f"ArcadeDB request timed out after {self._timeout}s"
            ) from exc

    async def execute_sql(
        self, command: str, database: str | None = None
    ) -> dict:
        """Execute a SQL command (DDL: CREATE VERTEX TYPE, CREATE PROPERTY, etc.)."""
        return await self.execute_query("sql", command, database)

    async def execute_cypher(
        self,
        query: str,
        database: str | None = None,
        params: dict | None = None,
    ) -> dict:
        """Execute an OpenCypher query (DML/DQL: MATCH, CREATE, MERGE, DELETE, etc.).

        CRITICAL: language is always 'opencypher', never 'cypher'.
        """
        return await self.execute_query("opencypher", query, database, params=params)
