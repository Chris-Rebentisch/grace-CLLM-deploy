"""Shared fixtures for extraction module tests."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.extraction.claim_models import (
    Claim,
    ClaimStatus,
    ClaimVerdict,
    EvidenceSpan,
)
from src.extraction.document_chunker import DocumentChunker
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import (
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionResult,
)
from src.shared.database import get_db, get_engine

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def db_session():
    """Scoped database session that rolls back after each test."""
    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()

    from sqlalchemy.orm import Session

    session = Session(bind=connection)
    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def clean_extraction_tables(db_session):
    """Ensure extraction tables are clean before test."""
    db_session.execute(text("DELETE FROM extraction_events_pg"))
    db_session.execute(text("DELETE FROM extraction_claims"))
    db_session.flush()
    return db_session


@pytest.fixture
def sample_ontology_schema() -> dict:
    """Minimal ontology JSON Schema with entity and relationship types."""
    return {
        "entity_types": {
            "Legal_Entity": {
                "properties": {
                    "name": {"type": "string"},
                    "jurisdiction": {"type": "string"},
                }
            },
            "Contract": {
                "properties": {
                    "title": {"type": "string"},
                    "effective_date": {"type": "string"},
                }
            },
        },
        "relationships": {
            "party_to": {
                "domain": "Legal_Entity",
                "range": "Contract",
            }
        },
    }


@pytest.fixture
def sample_extraction_result() -> ExtractionResult:
    """Valid ExtractionResult with entities and relationships."""
    return ExtractionResult(
        entities=[
            ExtractedEntity(
                name="Acme Corp",
                entity_type="Legal_Entity",
                properties={"jurisdiction": "Delaware"},
                source_sentence_indices=[0, 2],
            ),
            ExtractedEntity(
                name="Service Agreement",
                entity_type="Contract",
                properties={"effective_date": "2024-01-01"},
            ),
        ],
        relationships=[
            ExtractedRelationship(
                subject_name="Acme Corp",
                subject_type="Legal_Entity",
                predicate="party_to",
                object_name="Service Agreement",
                object_type="Contract",
                source_sentence_indices=[1],
            ),
        ],
    )


@pytest.fixture
def sample_claim() -> Claim:
    """Fully populated Claim for database tests."""
    claim_id = str(uuid4())
    return Claim(
        claim_id=claim_id,
        claim_fingerprint="a" * 64,
        extraction_unit_id="b" * 64,
        entity_type="Legal_Entity",
        relationship_type=None,
        subject_name="Acme Corp",
        predicate="entity",
        object_name=None,
        properties_json={"jurisdiction": "Delaware"},
        evidence_spans=[
            EvidenceSpan(
                sentence_index=0,
                text="Acme Corp is a Delaware corporation.",
                char_start=0,
                char_end=36,
            )
        ],
        verdict=ClaimVerdict.PENDING,
        confidence=None,
        status=ClaimStatus.AUTO_ACCEPTED,
        decision_source="pipeline",
        source_document_id="doc-001",
        source_chunk_id="chunk-001",
        ontology_module="core",
        schema_version=1,
        prompt_template_id="extraction_v1",
        model_name="qwen2.5:7b",
        model_temperature=0.0,
        model_max_tokens=4096,
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def sample_extraction_event() -> dict:
    """Extraction event dict for database tests."""
    return {
        "event_id": str(uuid4()),
        "batch_id": str(uuid4()),
        "source_document_id": "doc-001",
        "ontology_module": "core",
        "schema_version": 1,
        "provider_used": "ollama",
        "model_used": "qwen2.5:7b",
        "chunks_total": 5,
        "chunks_succeeded": 4,
        "chunks_failed": 1,
        "entities_extracted": 10,
        "relationships_extracted": 5,
        "claims_accepted": 12,
        "claims_quarantined": 3,
        "avg_confidence": 0.85,
        "started_at": datetime.now(UTC),
        "completed_at": None,
        "status": "running",
    }


@pytest.fixture
def extraction_settings() -> ExtractionSettings:
    """Default ExtractionSettings for tests."""
    return ExtractionSettings()


# --- Chunk 17 fixtures: Chunker + Docling ---


@pytest.fixture
def sample_docling_json():
    """Load a real Docling JSON fixture (from pre-step generation)."""
    # Try PDF first (richest structure), then DOCX
    for name in ("sample_pdf.json", "sample_docx.json"):
        fixture_path = FIXTURES_DIR / name
        if fixture_path.exists():
            return json.loads(fixture_path.read_text())
    return _synthetic_docling_json()


@pytest.fixture
def sample_extracted_text():
    """Load the extracted text corresponding to the Docling fixture."""
    for name in ("sample_pdf_text.txt", "sample_docx_text.txt"):
        fixture_path = FIXTURES_DIR / name
        if fixture_path.exists():
            return fixture_path.read_text()
    return _synthetic_extracted_text()


@pytest.fixture
def docx_docling_json():
    """Load the DOCX Docling JSON fixture."""
    fixture_path = FIXTURES_DIR / "sample_docx.json"
    if fixture_path.exists():
        return json.loads(fixture_path.read_text())
    return _synthetic_docling_json()


@pytest.fixture
def docx_extracted_text():
    """Load the DOCX extracted text."""
    fixture_path = FIXTURES_DIR / "sample_docx_text.txt"
    if fixture_path.exists():
        return fixture_path.read_text()
    return _synthetic_extracted_text()


@pytest.fixture
def chunker():
    """DocumentChunker with default settings."""
    return DocumentChunker()


@pytest.fixture
def chunker_small_cap():
    """DocumentChunker with small token cap for testing splits."""
    config = ExtractionSettings(chunk_token_cap=200, chunk_overlap_tokens=50)
    return DocumentChunker(config=config)


# --- Chunk 18 fixtures: Pipeline, Ollama probe, mocks ---


@pytest.fixture(scope="session")
def ollama_available() -> bool:
    """Check if Ollama is running. Session-scoped: probes once per test run."""
    import httpx

    try:
        # Match OpenAI-compatible path used by Instructor + Ollama (ExtractionLLMClient).
        r = httpx.get("http://localhost:11434/v1/models", timeout=2.0)
        if r.status_code != 200:
            return False
        data = r.json()
        return isinstance(data, dict) and "data" in data
    except (httpx.ConnectError, httpx.TimeoutException, ValueError):
        return False


@pytest.fixture
def require_ollama(ollama_available):
    """Skip test if Ollama is not running."""
    if not ollama_available:
        pytest.skip("Ollama not running on localhost:11434")


@pytest.fixture
def mock_extraction_client(sample_extraction_result):
    """Mock ExtractionLLMClient returning fixed ExtractionResult."""
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock()
    client.extract = AsyncMock(return_value=sample_extraction_result)
    client._extraction_provider = "ollama"
    client._extraction_model = "qwen2.5:7b"
    client.extraction_provider = "ollama"
    client.extraction_model = "qwen2.5:7b"
    return client


@pytest.fixture
def mock_ontology_router(sample_ontology_schema):
    """Mock OntologyRouter returning sample schema without API."""
    from unittest.mock import AsyncMock, MagicMock

    router = MagicMock()
    router.resolve_schema = AsyncMock(return_value=(sample_ontology_schema, None))
    router.get_available_modules = AsyncMock(return_value=["core"])
    return router


@pytest.fixture
def multi_chunk_document_text() -> str:
    """Long text producing 3+ chunks. Includes repeated entity names."""
    section1 = (
        "Acme Holdings entered into a service agreement with GlobalTech Solutions "
        "on January 15, 2024. The agreement covers cloud infrastructure management "
        "and data analytics services for a period of three years. Acme Holdings, "
        "headquartered in Delaware, has been seeking to modernize its technology "
        "stack across all business divisions. The initial contract value was "
        "estimated at twelve million dollars, with annual reviews scheduled for "
        "January of each subsequent year. GlobalTech Solutions brought extensive "
        "experience in enterprise cloud migration, having previously worked with "
        "several Fortune 500 companies on similar transformation projects.\n\n"
    ) * 8

    section2 = (
        "The Service Agreement between the parties specifies detailed service "
        "level agreements covering uptime guarantees, response times, and data "
        "protection standards. Both parties agreed to quarterly business reviews "
        "and monthly operational meetings. The contract includes provisions for "
        "early termination with a ninety-day notice period and specifies dispute "
        "resolution through binding arbitration in New York. Performance metrics "
        "are tracked through a shared dashboard accessible to both organizations.\n\n"
    ) * 8

    section3 = (
        "Acme Holdings appointed Sarah Chen as the primary relationship manager "
        "for the GlobalTech engagement. Chen, who serves as Vice President of "
        "Technology Operations, has overseen multiple vendor relationships during "
        "her tenure at Acme Holdings. Her team includes fifteen engineers dedicated "
        "to the cloud migration initiative. The technology roadmap spans three "
        "phases, with the first phase focusing on infrastructure assessment and "
        "the second phase covering actual migration of production workloads.\n\n"
    ) * 8

    section4 = (
        "GlobalTech Solutions assigned their senior delivery team led by Marcus "
        "Rodriguez to manage the Acme Holdings account. Rodriguez has led over "
        "twenty enterprise cloud transformations and holds multiple certifications "
        "in cloud architecture. The delivery team works closely with Acme Holdings "
        "to ensure seamless integration of new cloud services with existing "
        "on-premise systems. Regular status reports are provided to the Acme Holdings "
        "executive team documenting progress against the agreed milestones.\n\n"
    ) * 8

    return section1 + section2 + section3 + section4


@pytest.fixture
def pipeline_with_mocks(mock_extraction_client, mock_ontology_router, chunker):
    """ExtractionPipeline wired to mocked client and router."""
    from src.extraction.extraction_pipeline import ExtractionPipeline

    config = ExtractionSettings()
    return ExtractionPipeline(
        config=config,
        chunker=chunker,
        router=mock_ontology_router,
        client=mock_extraction_client,
    )


# --- Chunk 19 fixtures: Verification ---


@pytest.fixture
def mock_verification_result():
    """VerificationResult with SUPPORTED verdict and sample evidence."""
    from src.extraction.verification import VerificationResult

    return VerificationResult(
        chain_of_thought="The text states Acme is a Legal_Entity.",
        verdict="SUPPORTED",
        evidence_sentences=[0],
        contradiction_reason="",
    )


@pytest.fixture
def mock_verification_client(mock_verification_result):
    """Mock ExtractionLLMClient where verify() returns fixed result."""
    from unittest.mock import AsyncMock

    client = AsyncMock()
    client.verify = AsyncMock(return_value=mock_verification_result)
    client.extraction_provider = "ollama"
    client.extraction_model = "qwen2.5:7b"
    client.verification_provider = "ollama"
    client.verification_model = "qwen2.5:7b"
    return client


@pytest.fixture
def sample_chunks_for_verification():
    """List of DocumentChunks with known chunk_ids and sentence offsets."""
    from src.extraction.extraction_models import DocumentChunk

    return [
        DocumentChunk(
            chunk_id="chunk_0",
            text="Acme Corp is a legal entity. It was founded in 2019.",
            char_start=0,
            char_end=52,
            sentence_offsets=[(0, 27), (28, 52)],
            token_count_estimate=15,
        ),
        DocumentChunk(
            chunk_id="chunk_1",
            text="The lease agreement was signed. Acme is the tenant.",
            char_start=52,
            char_end=103,
            sentence_offsets=[(0, 30), (31, 51)],
            token_count_estimate=14,
        ),
    ]


@pytest.fixture
def multi_chunk_entities_for_dedup():
    """Two ExtractedEntity with same merge key from different chunks.

    Both use sentence index 2 — proves collision resolution via
    chunk_source_map after merge.
    """
    return [
        ExtractedEntity(
            name="Acme Corp",
            entity_type="Legal_Entity",
            source_sentence_indices=[2],
            chunk_source_map=[("chunk_0", 2)],
            properties={"jurisdiction": "Delaware"},
        ),
        ExtractedEntity(
            name="Acme Corp",
            entity_type="Legal_Entity",
            source_sentence_indices=[2],
            chunk_source_map=[("chunk_1", 2)],
            properties={"formation_date": "2019"},
        ),
    ]


def _synthetic_docling_json() -> dict:
    """Minimal synthetic Docling JSON for testing when real fixtures unavailable."""
    return {
        "schema_name": "DoclingDocument",
        "version": "1.0.0",
        "name": "synthetic_test",
        "body": {
            "self_ref": "#/body",
            "children": [
                {"cref": "#/texts/0"},
                {"cref": "#/texts/1"},
                {"cref": "#/texts/2"},
                {"cref": "#/texts/3"},
                {"cref": "#/texts/4"},
            ],
            "label": "body",
            "name": "body",
            "parent": None,
            "content_layer": "body",
            "meta": [],
        },
        "texts": [
            {
                "self_ref": "#/texts/0",
                "text": "Introduction",
                "label": "section_header",
                "parent": {"cref": "#/body"},
                "children": [],
                "content_layer": "body",
                "meta": [],
            },
            {
                "self_ref": "#/texts/1",
                "text": "This is the first paragraph of the introduction.",
                "label": "text",
                "parent": {"cref": "#/body"},
                "children": [],
                "content_layer": "body",
                "meta": [],
            },
            {
                "self_ref": "#/texts/2",
                "text": "This is the second paragraph.",
                "label": "text",
                "parent": {"cref": "#/body"},
                "children": [],
                "content_layer": "body",
                "meta": [],
            },
            {
                "self_ref": "#/texts/3",
                "text": "Methods",
                "label": "section_header",
                "parent": {"cref": "#/body"},
                "children": [],
                "content_layer": "body",
                "meta": [],
            },
            {
                "self_ref": "#/texts/4",
                "text": "We used a standard approach.",
                "label": "text",
                "parent": {"cref": "#/body"},
                "children": [],
                "content_layer": "body",
                "meta": [],
            },
        ],
        "tables": [],
        "groups": [],
        "pictures": [],
        "key_value_items": [],
        "form_items": [],
    }


def _synthetic_extracted_text() -> str:
    """Synthetic extracted text matching the synthetic Docling JSON."""
    return (
        "## Introduction\n\n"
        "This is the first paragraph of the introduction.\n\n"
        "This is the second paragraph.\n\n"
        "## Methods\n\n"
        "We used a standard approach.\n"
    )
