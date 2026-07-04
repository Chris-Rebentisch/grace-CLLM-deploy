"""PST adapter — delegates to PstPreconverter then MboxAdapter.

Chunk 55, D419. Registered as ``@register_adapter("pst")``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from src.ingestion.adapter_base import AdapterResult, EmailAdapter
from src.ingestion.adapter_registry import register_adapter
from src.ingestion.adapters.mbox_adapter import MboxAdapter
from src.ingestion.models import (
    IngestionCheckpoint,
    MboxSourceConfig,
    PstSourceConfig,
    SourceConfig,
)
from src.ingestion.pst_preconverter import PstPreconverter


@register_adapter("pst")
class PstAdapter(EmailAdapter):
    """Adapter for PST archives — converts to mbox then delegates."""

    source_type = "pst"

    def __init__(self, config: SourceConfig | None = None) -> None:
        super().__init__(config)
        self._mbox_adapter: MboxAdapter | None = None
        self._source_id = uuid4()

    async def connect(self, source_config: SourceConfig) -> None:
        assert isinstance(source_config, PstSourceConfig)
        self.config = source_config

        preconverter = PstPreconverter(
            pst_path=source_config.file_path,
            source_id=self._source_id,
            converted_output_dir=source_config.converted_output_dir,
        )
        out_dir = preconverter.convert()

        # Find mbox files in the output directory
        mbox_files = list(Path(out_dir).rglob("*.mbox")) + list(Path(out_dir).rglob("mbox"))
        if not mbox_files:
            # readpst may output files without .mbox extension
            mbox_files = [
                f for f in Path(out_dir).rglob("*")
                if f.is_file() and f.suffix not in {".log", ".txt"}
            ]

        if not mbox_files:
            raise FileNotFoundError(f"No mbox output found in {out_dir}")

        # Use the first mbox file
        mbox_config = MboxSourceConfig(file_path=str(mbox_files[0]))
        self._mbox_adapter = MboxAdapter()
        await self._mbox_adapter.connect(mbox_config)

    async def list_messages(self, *, limit: int | None = None) -> AsyncIterator[str]:
        assert self._mbox_adapter is not None
        async for msg_id in self._mbox_adapter.list_messages(limit=limit):
            yield msg_id

    async def parse_message(self, message_id: str) -> AdapterResult:
        assert self._mbox_adapter is not None
        return await self._mbox_adapter.parse_message(message_id)

    def checkpoint(self) -> IngestionCheckpoint:
        if self._mbox_adapter is not None:
            return self._mbox_adapter.checkpoint()
        return IngestionCheckpoint(checkpoint_type="file_offset", value="0")

    async def close(self) -> None:
        if self._mbox_adapter is not None:
            await self._mbox_adapter.close()
            self._mbox_adapter = None
