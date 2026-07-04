"""Tests for adapter registry (CP2)."""

from __future__ import annotations

import pytest

from src.ingestion.adapter_registry import _REGISTRY, get_adapter, list_registered, register_adapter
from src.ingestion.adapter_base import EmailAdapter
from src.ingestion.models import MboxSourceConfig


class _DummyAdapter(EmailAdapter):
    source_type = "test_dummy"

    async def connect(self, source_config):
        pass

    async def list_messages(self, *, limit=None):
        yield "msg1"

    async def parse_message(self, message_id):
        pass

    def checkpoint(self):
        pass

    async def close(self):
        pass


class TestAdapterRegistry:
    def setup_method(self):
        # Clean up any test registrations
        _REGISTRY.pop("test_dummy", None)
        _REGISTRY.pop("test_dup", None)

    def teardown_method(self):
        _REGISTRY.pop("test_dummy", None)
        _REGISTRY.pop("test_dup", None)

    def test_registration(self):
        register_adapter("test_dummy")(_DummyAdapter)
        assert "test_dummy" in _REGISTRY

    def test_duplicate_rejection(self):
        register_adapter("test_dup")(_DummyAdapter)
        with pytest.raises(ValueError, match="Duplicate adapter type"):
            register_adapter("test_dup")(_DummyAdapter)

    def test_get_adapter_returns_instance(self):
        register_adapter("test_dummy")(_DummyAdapter)
        config = MboxSourceConfig(file_path="/a.mbox")
        adapter = get_adapter("test_dummy", config)
        assert isinstance(adapter, _DummyAdapter)

    def test_get_adapter_unknown_raises(self):
        with pytest.raises(KeyError, match="Unknown adapter type"):
            get_adapter("nonexistent", MboxSourceConfig(file_path="/a.mbox"))
