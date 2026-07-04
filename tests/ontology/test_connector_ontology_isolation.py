"""Regression tests for F-0045 / ISS-0025 — connector-sync ontology isolation.

Invariant under test: a CHILD-namespace connector sync (or any
child-scoped ratification) must NEVER become or replace the deployment's
(mother's) active ontology version. Three consecutive validation runs saw
``python -m src.connectors run --connector-type synthetic`` ratify a
0-entity-type child schema as the deployment-active ontology, breaking
every module-scoped consumer ("Schema not found for module_name=...").

Pure unit tests — all DB CRUD functions are patched at the
``src.ontology.schema_store`` namespace; no Postgres, no ArcadeDB.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.ontology.models import OntologyVersion, VersionSource
from src.ontology.schema_store import ratify_version

MOTHER_SCHEMA = {
    "entity_types": {
        "Legal_Entity": {"properties": {"name": {"data_type": "string"}}},
        "Trust": {"properties": {"name": {"data_type": "string"}}},
    },
    "relationships": {},
}

CHILD_SCHEMA = {
    "entity_types": {},
    "relationships": {},
}


def _mother_version() -> OntologyVersion:
    """Simulated currently-active deployment (mother) ontology version."""
    return OntologyVersion(
        id=uuid4(),
        version_number=6,
        schema_json=MOTHER_SCHEMA,
        schema_modules={"my_domain": MOTHER_SCHEMA},
        hash_chain="a" * 64,
        source=VersionSource.GUIDED_REVIEW,
        is_active=True,
    )


@pytest.fixture()
def store_mocks():
    """Patch every DB/diff/validation collaborator in schema_store's namespace."""
    mother = _mother_version()
    passing_validation = MagicMock(passed=True, type_results=[])
    with (
        patch(
            "src.ontology.schema_store.get_active_version", return_value=mother
        ) as get_active,
        patch(
            "src.ontology.schema_store.get_next_version_number", return_value=7
        ),
        patch(
            "src.ontology.schema_store.create_version",
            side_effect=lambda db, v: v,
        ) as create,
        patch("src.ontology.schema_store.set_active_version") as set_active,
        patch(
            "src.ontology.schema_store.compute_schema_diff",
            return_value=(None, None),
        ),
        patch(
            "src.ontology.schema_store.validate_child_ontology_submission",
            return_value=passing_validation,
        ),
        patch(
            "src.ontology.schema_store._emit_ontology_entity_type_count"
        ) as emit_gauge,
    ):
        yield {
            "mother": mother,
            "get_active": get_active,
            "create": create,
            "set_active": set_active,
            "emit_gauge": emit_gauge,
        }


def _run_connector_sync_ratification(activate_kwarg: dict) -> OntologyVersion:
    """Simulate the sync_pipeline ratify call (source=connector_sync, child scope)."""
    return ratify_version(
        MagicMock(),  # db session — never reached, all CRUD patched
        schema_json=CHILD_SCHEMA,
        schema_modules={"synthetic": CHILD_SCHEMA},
        source="connector_sync",
        reviewer=None,
        changelog="Connector sync: synthetic",
        ontology_scope="child",
        **activate_kwarg,
    )


# ---------------------------------------------------------------------------
# 1. The sync_pipeline form (activate=False) never touches the active flag.
# ---------------------------------------------------------------------------


def test_connector_sync_ratification_leaves_mother_active(store_mocks) -> None:
    """F-0045: after a connector-sync ratification the mother stays active."""
    version = _run_connector_sync_ratification({"activate": False})

    # The active-flag swap must never run — the previously active mother
    # version is therefore still the active one.
    store_mocks["set_active"].assert_not_called()

    # The connector version DOES exist (provenance) but is inactive + child-scoped.
    store_mocks["create"].assert_called_once()
    persisted: OntologyVersion = store_mocks["create"].call_args.args[1]
    assert persisted.is_active is False
    assert version.is_active is False
    assert version.source == VersionSource.CONNECTOR_SYNC
    assert version.metadata_extra.get("ontology_scope") == "child"
    assert store_mocks["mother"].is_active is True


# ---------------------------------------------------------------------------
# 2. Belt-and-braces guard: a DIRECT activation attempt is refused.
# ---------------------------------------------------------------------------


def test_guard_refuses_connector_sync_activation_attempt(store_mocks) -> None:
    """F-0045 guard: connector_sync + activate=True must still not activate."""
    version = _run_connector_sync_ratification({"activate": True})
    store_mocks["set_active"].assert_not_called()
    assert version.is_active is False


def test_guard_refuses_connector_sync_default_activation(store_mocks) -> None:
    """F-0045 guard: connector_sync with the activate default must not activate."""
    version = _run_connector_sync_ratification({})
    store_mocks["set_active"].assert_not_called()
    assert version.is_active is False


def test_guard_refuses_child_scope_regardless_of_source(store_mocks) -> None:
    """D405 child scope never becomes deployment-active, even source=manual."""
    version = ratify_version(
        MagicMock(),
        schema_json=CHILD_SCHEMA,
        schema_modules={"child_mod": CHILD_SCHEMA},
        source=VersionSource.MANUAL,
        ontology_scope="child",
        activate=True,
    )
    store_mocks["set_active"].assert_not_called()
    assert version.is_active is False


def test_guard_refuses_connector_sync_enum_without_child_scope(store_mocks) -> None:
    """Guard keys on source too: CONNECTOR_SYNC without scope still refused."""
    version = ratify_version(
        MagicMock(),
        schema_json=CHILD_SCHEMA,
        schema_modules={"synthetic": CHILD_SCHEMA},
        source=VersionSource.CONNECTOR_SYNC,
    )
    store_mocks["set_active"].assert_not_called()
    assert version.is_active is False


# ---------------------------------------------------------------------------
# 3. Non-activated versions must not clobber the promotion gauge.
# ---------------------------------------------------------------------------


def test_gauge_not_emitted_for_non_activated_child_version(store_mocks) -> None:
    """Replace-in-full gauge is keyed to the ACTIVE ontology; child skips it."""
    _run_connector_sync_ratification({"activate": False})
    store_mocks["emit_gauge"].assert_not_called()


# ---------------------------------------------------------------------------
# 4. Backwards compatibility: normal ratifications still activate.
# ---------------------------------------------------------------------------


def test_default_ratification_still_activates(store_mocks) -> None:
    """Legacy callers (no scope, non-connector source) keep today's behavior."""
    activated_sentinel = _mother_version()
    store_mocks["set_active"].return_value = activated_sentinel

    result = ratify_version(
        MagicMock(),
        schema_json=MOTHER_SCHEMA,
        schema_modules={"my_domain": MOTHER_SCHEMA},
        source=VersionSource.GUIDED_REVIEW,
        reviewer="operator",
    )

    store_mocks["set_active"].assert_called_once()
    store_mocks["emit_gauge"].assert_called_once()
    assert result is activated_sentinel
    # Default-scope versions carry no scope marker in metadata_extra.
    persisted: OntologyVersion = store_mocks["create"].call_args.args[1]
    assert "ontology_scope" not in persisted.metadata_extra
