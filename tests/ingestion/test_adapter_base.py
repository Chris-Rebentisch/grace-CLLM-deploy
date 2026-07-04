"""Tests for EmailAdapter ABC (CP2)."""

from __future__ import annotations

import pytest

from src.ingestion.adapter_base import AdapterResult, EmailAdapter
from src.ingestion.models import CommunicationEvent
from uuid import uuid4


class TestEmailAdapterABC:
    def test_abc_rejects_incomplete_subclass(self):
        """ABC guard rejects subclass that doesn't implement all 5 methods."""

        class IncompleteAdapter(EmailAdapter):
            source_type = "incomplete"

        with pytest.raises(TypeError):
            IncompleteAdapter()

    def test_init_subclass_rejects_missing_source_type(self):
        """__init_subclass__ rejects subclass without source_type."""
        with pytest.raises(TypeError, match="must define a 'source_type' class attribute"):

            class NoSourceType(EmailAdapter):
                pass

    def test_five_method_contract(self):
        """EmailAdapter defines exactly five abstract methods."""
        abstract_methods = EmailAdapter.__abstractmethods__
        assert abstract_methods == {"connect", "list_messages", "parse_message", "checkpoint", "close"}

    def test_adapter_result_shape(self):
        """AdapterResult has expected fields."""
        evt = CommunicationEvent(
            source_id=uuid4(),
            message_id="<test@example.com>",
            sender_email="alice@example.com",
            source_type="mbox",
        )
        result = AdapterResult(event=evt)
        assert result.warnings == []
        assert result.checkpoint_value is None
