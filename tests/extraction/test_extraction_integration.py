"""Integration tests using real Ollama. Marked @slow — skipped when Ollama unavailable."""

import pytest

from src.extraction.document_chunker import DocumentChunker
from src.extraction.eval_checkpoint import FileSchemaRouter
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import DocumentChunk, ExtractionResult
from src.extraction.extraction_pipeline import ExtractionPipeline
from src.extraction.extractor import extract_chunk
from src.extraction.instructor_client import ExtractionLLMClient


class TestOllamaIntegration:
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_real_extraction_ollama(self, require_ollama, sample_ontology_schema):
        """Real Ollama call produces valid ExtractionResult."""
        config = ExtractionSettings()
        client = ExtractionLLMClient(config)

        chunk = DocumentChunk(
            chunk_id="integration-test-001",
            text="Acme Corp is a Delaware corporation. It signed a service agreement in January 2024.",
            char_start=0,
            char_end=82,
            sentence_offsets=[(0, 36), (37, 82)],
        )

        result = await extract_chunk(chunk, sample_ontology_schema, client, config)

        assert result.success is True
        assert isinstance(result.result, ExtractionResult)
        assert result.latency_ms > 0

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_real_pipeline_ollama(self, require_ollama, sample_ontology_schema):
        """Real pipeline produces ExtractionBatch from short document."""
        config = ExtractionSettings()
        chunker = DocumentChunker(config)
        router = FileSchemaRouter(sample_ontology_schema)
        client = ExtractionLLMClient(config)

        pipeline = ExtractionPipeline(
            config=config, chunker=chunker, router=router, client=client
        )

        text = (
            "Acme Corp is a Delaware corporation founded in 2020. "
            "It entered into a Master Service Agreement with GlobalTech Solutions "
            "effective January 1, 2024. The agreement covers cloud infrastructure "
            "and data analytics services."
        )

        batch = await pipeline.extract_document(text, "integration-test-doc", verify=False)

        # ISS-0003: under a full-suite run the local LLM can fail a single
        # chunk (load-induced timeout/nondeterminism) — this test passes in
        # isolation. One retry distinguishes a flaky model call from a real
        # pipeline regression (which fails both attempts deterministically).
        if batch.chunks_succeeded < 1:
            batch = await pipeline.extract_document(
                text, "integration-test-doc-retry", verify=False
            )

        assert batch.chunks_total >= 1
        assert batch.chunks_succeeded >= 1
