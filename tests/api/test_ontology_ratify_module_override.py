"""F-0010 / ISS-0046 — ratify-surface `module_name` override.

Module names were an emergent property of free-text authoring `domain`
strings. `POST /api/ontology/ratify` now accepts an optional additive
`module_name` field: the server normalizes every element's domain to
that name and recomputes `schema_modules` server-side.

Pure unit tests — `ratify_version` and the elicitation bridge are
patched, `get_db` is overridden with a mock session. No Postgres.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.shared.database import get_db


@pytest.fixture()
def client():
    """TestClient with a mocked DB session (no Postgres)."""
    mock_session = MagicMock()

    def override_get_db():
        yield mock_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _stub_version() -> MagicMock:
    version = MagicMock()
    version.id = uuid4()
    version.version_number = 2
    version.model_dump.return_value = {"id": str(version.id), "version_number": 2}
    return version


def _body(**overrides) -> dict:
    defaults = {
        "schema_json": {
            "entity_types": {
                "Company": {"domain": "finance"},
                "Trust": {"domain": None},
            },
            "relationships": {
                "owns": {
                    "domain": "legal",
                    "source_type": "Company",
                    "target_type": "Trust",
                },
            },
        },
        "schema_modules": {"client_supplied": {"entity_types": {}, "relationships": {}}},
        "source": "manual",
        "reviewer": "test_user",
        "changelog": "ISS-0046 test",
    }
    defaults.update(overrides)
    return defaults


def _post_ratify(client: TestClient, body: dict) -> tuple[int, MagicMock]:
    with (
        patch(
            "src.api.ontology_routes.ratify_version",
            return_value=_stub_version(),
        ) as mock_ratify,
        patch("src.elicitation.bridge.enqueue_event"),
    ):
        resp = client.post("/api/ontology/ratify", json=body)
    return resp.status_code, mock_ratify


def test_module_name_normalizes_domains_and_repartitions(client):
    """module_name: all element domains normalized; modules recomputed server-side."""
    status_code, mock_ratify = _post_ratify(
        client, _body(module_name="my_domain")
    )

    assert status_code == 200
    kwargs = mock_ratify.call_args.kwargs
    schema_json = kwargs["schema_json"]
    # Every element's domain — including present-but-null and real ones —
    # is normalized to the override.
    assert schema_json["entity_types"]["Company"]["domain"] == "my_domain"
    assert schema_json["entity_types"]["Trust"]["domain"] == "my_domain"
    assert schema_json["relationships"]["owns"]["domain"] == "my_domain"
    # schema_modules is recomputed server-side (client value ignored):
    # a single module keyed by the override, carrying all elements.
    modules = kwargs["schema_modules"]
    assert set(modules) == {"my_domain"}
    assert set(modules["my_domain"]["entity_types"]) == {"Company", "Trust"}
    assert set(modules["my_domain"]["relationships"]) == {"owns"}


def test_without_module_name_passthrough_unchanged(client):
    """Additive contract: omitting module_name keeps the old passthrough."""
    body = _body()
    status_code, mock_ratify = _post_ratify(client, body)

    assert status_code == 200
    kwargs = mock_ratify.call_args.kwargs
    assert kwargs["schema_json"] == body["schema_json"]
    assert kwargs["schema_modules"] == body["schema_modules"]


def test_normalize_schema_domains_is_copy_on_write():
    """The helper never mutates the caller's schema_json in place."""
    from src.api.ontology_routes import _normalize_schema_domains

    schema_json = {
        "entity_types": {"Company": {"domain": "finance"}},
        "relationships": {"owns": {"domain": None}},
    }

    normalized = _normalize_schema_domains(schema_json, "my_domain")

    assert normalized["entity_types"]["Company"]["domain"] == "my_domain"
    assert normalized["relationships"]["owns"]["domain"] == "my_domain"
    # Source payload untouched.
    assert schema_json["entity_types"]["Company"]["domain"] == "finance"
    assert schema_json["relationships"]["owns"]["domain"] is None
