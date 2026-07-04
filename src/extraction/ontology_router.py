"""Ontology module router for the Extraction pipeline.

Fetches the correct ontology module schema for injection into extraction
prompts. Supports explicit module routing and full active schema retrieval.

Auto-detect routing (embedding-based) is deferred to Chunk 17b.
"""

from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger()


class OntologyRouterError(Exception):
    """Raised when the Ontology Management API is unreachable or returns a
    non-recoverable error (connection failure, 500+, malformed response)."""


class OntologyRouter:
    """Routes extraction requests to the correct ontology module schema.

    Uses the existing Ontology Management API endpoints built in Phase 2.
    All calls are async via httpx.AsyncClient.
    """

    def __init__(self, base_url: str = "http://localhost:8000"):
        """Initialize with FastAPI base URL for Ontology Management API."""
        self._base_url = base_url.rstrip("/")
        self._timeout = 10.0

    async def route_explicit(self, module_name: str) -> dict | None:
        """Fetch schema for a named ontology module.

        Calls GET /api/ontology/modules/{module_name}.
        Returns JSON Schema dict for the module, or None if not found (404).
        Raises OntologyRouterError on connection failure or server error.
        """
        response = await self._get(f"/api/ontology/modules/{module_name}")
        if response.status_code == 404:
            log.info("module_not_found", module_name=module_name)
            return None
        return response.json()

    async def get_active_schema(self) -> dict | None:
        """Fetch the full active production schema.

        Calls GET /api/ontology/active.
        Returns full version dict (includes schema_json, schema_modules,
        version_number, etc.).
        Returns None if no active version exists (404).
        Raises OntologyRouterError on connection failure or server error.
        """
        response = await self._get("/api/ontology/active")
        if response.status_code == 404:
            log.info("no_active_schema_version")
            return None
        return response.json()

    async def get_available_modules(self) -> list[str]:
        """Get list of module names from active schema.

        Calls GET /api/ontology/active, extracts keys from schema_modules.
        Returns empty list if no active version or no modules defined.
        """
        version = await self.get_active_schema()
        if not version:
            return []
        modules = version.get("schema_modules")
        if not modules or not isinstance(modules, dict):
            return []
        return list(modules.keys())

    async def resolve_schema(
        self,
        module_name: str | None = None,
    ) -> tuple[dict | None, int | None]:
        """Resolve ontology schema for extraction.

        Returns (schema_dict, version_number) or (None, None) if unavailable.

        Uses get_active_schema() (D80a) to obtain version_number alongside
        the schema. For module_name routing, extracts the module from
        schema_modules within the active version payload.
        """
        version = await self.get_active_schema()
        if not version:
            return (None, None)

        version_int = version.get("version_number")

        if module_name is None:
            return (version.get("schema_json"), version_int)

        modules = version.get("schema_modules")
        if not isinstance(modules, dict):
            return (None, None)
        schema = modules.get(module_name)
        if schema is None:
            return (None, None)
        return (schema, version_int)

    async def _get(self, path: str) -> httpx.Response:
        """Make a GET request with error handling.

        Raises OntologyRouterError on:
        - Connection refused / timeout (httpx.ConnectError, httpx.TimeoutException)
        - Server errors (5xx status codes)
        - Malformed responses (JSON decode failure)

        Returns the response for 2xx and 4xx status codes.
        Caller handles 404 (returns None) vs 2xx (returns data).
        """
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url)
        except httpx.ConnectError as e:
            raise OntologyRouterError(
                f"Connection failed to {url}: {e}"
            ) from e
        except httpx.TimeoutException as e:
            raise OntologyRouterError(
                f"Request timed out for {url}: {e}"
            ) from e

        if response.status_code >= 500:
            raise OntologyRouterError(
                f"Server error {response.status_code} from {url}"
            )

        # Verify response is valid JSON for success responses
        if response.status_code < 400:
            try:
                response.json()
            except ValueError as e:
                raise OntologyRouterError(
                    f"Malformed JSON response from {url}: {e}"
                ) from e

        return response
