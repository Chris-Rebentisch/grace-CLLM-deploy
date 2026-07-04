"""Tests for eval checkpoint metrics computation."""

from datetime import UTC, datetime

from src.extraction.claim_models import Claim, ClaimStatus, ClaimVerdict, EvidenceSpan
from src.extraction.eval_checkpoint import compute_metrics
from src.extraction.extraction_models import (
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionBatch,
)


def _make_batch(
    entities=None,
    relationships=None,
    chunks_total=1,
    chunks_succeeded=1,
    chunks_failed=0,
    entities_pre_dedup_count=None,
    relationships_pre_dedup_count=None,
    chunk_extraction_succeeded=None,
    chunk_entity_counts=None,
    chunk_relationship_counts=None,
    chunk_latency_ms=None,
):
    ents = entities or []
    rels = relationships or []
    n = chunks_total

    if n <= 0:
        ce: list[bool] = []
        ch_ec: list[int] = []
        ch_rc: list[int] = []
        ch_lat: list[float] = []
    else:
        if chunk_extraction_succeeded is None:
            chunk_extraction_succeeded = [i < chunks_succeeded for i in range(n)]
        if chunk_entity_counts is None:
            chunk_entity_counts = [0] * n
            if ents:
                for i in range(n):
                    if chunk_extraction_succeeded[i]:
                        chunk_entity_counts[i] = len(ents)
                        break
        if chunk_relationship_counts is None:
            chunk_relationship_counts = [0] * n
            if rels:
                for i in range(n):
                    if chunk_extraction_succeeded[i]:
                        chunk_relationship_counts[i] = len(rels)
                        break
        if chunk_latency_ms is None:
            chunk_latency_ms = [
                100.0 if chunk_extraction_succeeded[i] else 0.0
                for i in range(n)
            ]
        ce = chunk_extraction_succeeded
        ch_ec = chunk_entity_counts
        ch_rc = chunk_relationship_counts
        ch_lat = chunk_latency_ms

    return ExtractionBatch(
        document_id="test-doc",
        chunks_total=chunks_total,
        chunks_succeeded=chunks_succeeded,
        chunks_failed=chunks_failed,
        chunk_extraction_succeeded=ce,
        chunk_entity_counts=ch_ec,
        chunk_relationship_counts=ch_rc,
        chunk_latency_ms=ch_lat,
        entities_pre_dedup_count=(
            entities_pre_dedup_count if entities_pre_dedup_count is not None else len(ents)
        ),
        relationships_pre_dedup_count=(
            relationships_pre_dedup_count
            if relationships_pre_dedup_count is not None
            else len(rels)
        ),
        entities=ents,
        relationships=rels,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        completed_at=datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
    )


class TestEvalMetrics:
    def test_metrics_dict_has_required_keys(self):
        """Computed metrics dict contains all D67 keys."""
        batch = _make_batch()
        metrics = compute_metrics([batch], {}, "ollama", "qwen2.5:7b", "test.json")

        required_keys = {
            "provider", "model", "schema_file", "documents_processed",
            "total_chunks", "total_entities_pre_dedup", "total_entities_post_dedup",
            "total_relationships_pre_dedup", "total_relationships_post_dedup",
            "parse_success_rate", "schema_legal_entity_rate",
            "schema_legal_relationship_rate", "entities_per_chunk",
            "relationships_per_chunk", "duplicate_entity_rate",
            "duplicate_relationship_rate", "chunks_failed", "avg_latency_ms",
            "timestamp",
        }
        assert required_keys.issubset(set(metrics.keys()))

    def test_parse_success_rate_calculation(self):
        """3 success + 1 fail -> 0.75 parse_success_rate."""
        batches = [
            _make_batch(chunks_total=4, chunks_succeeded=3, chunks_failed=1),
        ]
        metrics = compute_metrics(batches, {}, "ollama", "m", "s")
        assert metrics["parse_success_rate"] == 0.75

    def test_schema_legal_entity_rate(self):
        """2 legal + 1 illegal entity type -> ~0.667 rate."""
        schema = {"entity_types": {"Legal_Entity": {}}, "relationships": {}}
        entities = [
            ExtractedEntity(name="A", entity_type="Legal_Entity"),
            ExtractedEntity(name="B", entity_type="Legal_Entity"),
            ExtractedEntity(name="C", entity_type="Unknown_Type"),
        ]
        batch = _make_batch(entities=entities)
        metrics = compute_metrics([batch], schema, "ollama", "m", "s")
        assert abs(metrics["schema_legal_entity_rate"] - 2 / 3) < 0.01

    def test_schema_legal_entity_null_when_unknown(self):
        """Empty entity type set -> schema_legal_entity_rate is None."""
        schema = {"foo": "bar"}  # unrecognized format
        entities = [ExtractedEntity(name="A", entity_type="T")]
        batch = _make_batch(entities=entities)
        metrics = compute_metrics([batch], schema, "ollama", "m", "s")
        assert metrics["schema_legal_entity_rate"] is None

    def test_schema_legal_relationship_null_when_unknown(self):
        """Empty predicate set -> schema_legal_relationship_rate is None."""
        schema = {"$defs": {"Legal_Entity": {}}}  # no relationships extractable
        rels = [
            ExtractedRelationship(
                subject_name="A", subject_type="T",
                predicate="rel", object_name="B", object_type="T",
            ),
        ]
        batch = _make_batch(relationships=rels)
        metrics = compute_metrics([batch], schema, "ollama", "m", "s")
        assert metrics["schema_legal_relationship_rate"] is None

    def test_duplicate_entity_rate(self):
        """Pre=10, post=8 -> rate=0.2."""
        entities = [ExtractedEntity(name=f"E{i}", entity_type="T") for i in range(8)]
        batch = _make_batch(
            entities=entities,
            entities_pre_dedup_count=10,
        )
        metrics = compute_metrics([batch], {}, "ollama", "m", "s")
        assert abs(metrics["duplicate_entity_rate"] - 0.2) < 0.01

    def test_entities_per_chunk_and_avg_latency_from_chunk_stats(self):
        """Per-chunk metrics use pipeline chunk_* fields, not document totals."""
        batch = ExtractionBatch(
            document_id="multi",
            chunks_total=2,
            chunks_succeeded=2,
            chunks_failed=0,
            chunk_extraction_succeeded=[True, True],
            chunk_entity_counts=[2, 4],
            chunk_relationship_counts=[1, 0],
            chunk_latency_ms=[10.0, 30.0],
            entities_pre_dedup_count=6,
            relationships_pre_dedup_count=1,
            entities=[
                ExtractedEntity(name="x", entity_type="T"),
                ExtractedEntity(name="y", entity_type="T"),
            ],
            relationships=[],
        )
        metrics = compute_metrics([batch], {}, "ollama", "m", "s")
        assert metrics["entities_per_chunk"]["mean"] == 3.0
        assert metrics["entities_per_chunk"]["min"] == 2
        assert metrics["entities_per_chunk"]["max"] == 4
        assert metrics["relationships_per_chunk"]["mean"] == 0.5
        assert abs(metrics["avg_latency_ms"] - 20.0) < 0.001


def _make_claim(verdict=ClaimVerdict.SUPPORTED, confidence=0.85, n_spans=1):
    """Helper to create a Claim with given verdict/confidence."""
    spans = [
        EvidenceSpan(sentence_index=i, text=f"s{i}", char_start=0, char_end=2)
        for i in range(n_spans)
    ]
    return Claim(
        subject_name="Test",
        predicate="entity",
        verdict=verdict,
        confidence=confidence,
        status=(
            ClaimStatus.QUARANTINED if verdict == ClaimVerdict.REFUTED
            else ClaimStatus.AUTO_ACCEPTED
        ),
        evidence_spans=spans,
        source_document_id="doc",
        source_chunk_id="c0",
    )


class TestEvalV2Metrics:
    def test_verify_flag_adds_metrics(self):
        """--verify adds verification-specific keys to output dict."""
        claims = [
            _make_claim(ClaimVerdict.SUPPORTED, 0.9, 2),
            _make_claim(ClaimVerdict.REFUTED, 0.05, 1),
            _make_claim(ClaimVerdict.INSUFFICIENT, 0.3, 0),
        ]
        batch = _make_batch(
            entities=[ExtractedEntity(name="E", entity_type="T")],
        )
        batch.claims = claims
        batch.verification_failure_count = 0
        metrics = compute_metrics(
            [batch], {}, "ollama", "m", "s",
            verify=True,
            verification_provider="ollama",
            verification_model="qwen2.5:7b",
        )
        assert "verifier_contradiction_rate" in metrics
        assert "verdict_distribution" in metrics
        assert "avg_evidence_span_count" in metrics
        assert "verification_failure_count" in metrics
        assert "verification_provider" in metrics
        assert "verification_model" in metrics

    def test_verdict_distribution_keys(self):
        """verdict_distribution has SUPPORTED, REFUTED, INSUFFICIENT keys."""
        claims = [_make_claim(ClaimVerdict.SUPPORTED, 0.9)]
        batch = _make_batch()
        batch.claims = claims
        batch.verification_failure_count = 0
        metrics = compute_metrics(
            [batch], {}, "ollama", "m", "s", verify=True,
        )
        vd = metrics["verdict_distribution"]
        assert "SUPPORTED" in vd
        assert "REFUTED" in vd
        assert "INSUFFICIENT" in vd
        assert vd["SUPPORTED"] == 1

    def test_v1_metrics_still_present(self):
        """v1 keys (parse_success_rate, etc.) still present when --verify."""
        batch = _make_batch()
        batch.claims = []
        batch.verification_failure_count = 0
        metrics = compute_metrics(
            [batch], {}, "ollama", "m", "s", verify=True,
        )
        assert "parse_success_rate" in metrics
        assert "total_entities_post_dedup" in metrics
        assert "duplicate_entity_rate" in metrics


# ── F-0008/F-0009 / ISS-0041: --doc-file selection + --persist session ─────
#
# Pure unit tests: ExtractionPipeline / ExtractionLLMClient / the session
# factory are mocked — no Postgres, no ArcadeDB, no Ollama.

import asyncio
import sys as _sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.extraction.eval_checkpoint import (
    SUPPORTED_DOC_SUFFIXES,
    _build_argparser,
    main,
    run_eval,
)


def _fake_llm_client():
    client = MagicMock()
    client.extraction_provider = "mock"
    client.extraction_model = "mock-model"
    client.verification_provider = "mock"
    client.verification_model = "mock-model"
    return client


def _run_eval_mocked(argv, extract_document):
    """Run run_eval with the pipeline and LLM client mocked out."""
    args = _build_argparser().parse_args(argv)
    pipeline = MagicMock()
    pipeline.extract_document = extract_document
    with patch(
        "src.extraction.eval_checkpoint.ExtractionLLMClient",
        return_value=_fake_llm_client(),
    ), patch(
        "src.extraction.eval_checkpoint.ExtractionPipeline",
        return_value=pipeline,
    ):
        return asyncio.run(run_eval(args))


def _base_argv(tmp_path, extra):
    schema = tmp_path / "schema.json"
    if not schema.exists():
        schema.write_text("{}")
    out = tmp_path / "out.json"
    return ["--schema", str(schema), "--output", str(out), *extra]


class TestDocFileSelection:
    def test_argparser_accepts_doc_file_and_persist(self, tmp_path):
        """New flags parse; defaults keep legacy behavior (ISS-0041)."""
        ns = _build_argparser().parse_args(
            ["--schema", "s.json", "--doc-file", "a.txt", "--persist"]
        )
        assert ns.doc_file == "a.txt"
        assert ns.persist is True
        ns2 = _build_argparser().parse_args(["--schema", "s.json"])
        assert ns2.doc_file is None
        assert ns2.persist is False

    def test_doc_file_extracts_exactly_named_file(self, tmp_path):
        """F-0008 / ISS-0041: the named file is extracted — NOT the
        alphabetically-first .txt/.md in its directory."""
        (tmp_path / "aaa_decoy.txt").write_text("decoy — old code extracted me")
        target = tmp_path / "zzz_target.txt"
        target.write_text("the requested document")

        extract = AsyncMock(return_value=_make_batch())
        _run_eval_mocked(
            _base_argv(tmp_path, ["--doc-file", str(target)]), extract
        )

        extract.assert_called_once()
        args, kwargs = extract.call_args
        assert args[0] == "the requested document"
        assert args[1] == "zzz_target.txt"

    def test_doc_file_unsupported_suffix_exits(self, tmp_path):
        """F-0008 / ISS-0041: binary suffixes error clearly (exit 1) — this
        CLI has no Docling path and must not pretend otherwise."""
        pdf = tmp_path / "claim.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        with pytest.raises(SystemExit) as exc_info:
            _run_eval_mocked(
                _base_argv(tmp_path, ["--doc-file", str(pdf)]), AsyncMock()
            )
        assert exc_info.value.code == 1

    def test_doc_file_missing_exits(self, tmp_path):
        """--doc-file pointing at a nonexistent path errors clearly."""
        with pytest.raises(SystemExit) as exc_info:
            _run_eval_mocked(
                _base_argv(tmp_path, ["--doc-file", str(tmp_path / "nope.txt")]),
                AsyncMock(),
            )
        assert exc_info.value.code == 1

    def test_supported_suffixes_are_text_only(self):
        """Guard: the honest suffix set stays plain-text until a real
        Docling path lands (route 422 message depends on this)."""
        assert set(SUPPORTED_DOC_SUFFIXES) == {".txt", ".md"}


class TestPersistFlag:
    def test_persist_builds_session_passes_it_and_commits(self, tmp_path):
        """F-0009 / ISS-0041: --persist constructs a real-session pipeline
        (get_session_factory()()), passes it to extract_document, commits
        (D84: pipeline never commits), and closes the session."""
        target = tmp_path / "doc.txt"
        target.write_text("persist me")

        session = MagicMock()
        factory = MagicMock(return_value=session)
        extract = AsyncMock(return_value=_make_batch())

        with patch(
            "src.shared.database.get_session_factory", return_value=factory
        ):
            _run_eval_mocked(
                _base_argv(tmp_path, ["--doc-file", str(target), "--persist"]),
                extract,
            )

        _, kwargs = extract.call_args
        assert kwargs["session"] is session
        # --persist implies --verify (claims only exist after verification)
        assert kwargs["verify"] is True
        session.commit.assert_called_once()
        session.close.assert_called_once()

    def test_without_persist_session_is_none(self, tmp_path):
        """Legacy eval behavior unchanged: no --persist → session=None."""
        target = tmp_path / "doc.txt"
        target.write_text("eval only")
        extract = AsyncMock(return_value=_make_batch())

        _run_eval_mocked(_base_argv(tmp_path, ["--doc-file", str(target)]), extract)

        _, kwargs = extract.call_args
        assert kwargs["session"] is None


class TestFromProcessedDoc:
    """F-0008 / ISS-0041 (binary-format follow-up): --from-processed-doc
    sources text from processed_documents.extracted_text (Docling batch
    output) instead of Path.read_text(). All DB access mocked."""

    def test_argparser_accepts_from_processed_doc(self):
        ns = _build_argparser().parse_args(
            ["--schema", "s.json", "--doc-file", "a.pdf", "--from-processed-doc"]
        )
        assert ns.from_processed_doc is True
        ns2 = _build_argparser().parse_args(["--schema", "s.json"])
        assert ns2.from_processed_doc is False

    def test_binary_doc_served_from_processed_documents(self, tmp_path):
        """A .pdf on disk (unreadable as text) extracts using the row's
        extracted_text; the lookup session is read-only and closed."""
        pdf = tmp_path / "claim.pdf"
        pdf.write_bytes(b"%PDF-1.4 \xff\xfe binary")  # read_text() would fail

        session = MagicMock()
        factory = MagicMock(return_value=session)
        processed = MagicMock()
        processed.extracted_text = "docling extracted this text"
        lookup = MagicMock(return_value=processed)
        extract = AsyncMock(return_value=_make_batch())

        with patch(
            "src.shared.database.get_session_factory", return_value=factory
        ), patch("src.discovery.database.get_document_by_path", lookup):
            _run_eval_mocked(
                _base_argv(
                    tmp_path, ["--doc-file", str(pdf), "--from-processed-doc"]
                ),
                extract,
            )

        # Lookup keyed on the RESOLVED path (batch runner's storage form).
        lookup.assert_called_once_with(session, str(pdf.resolve()))
        session.close.assert_called_once()
        args, _ = extract.call_args
        assert args[0] == "docling extracted this text"
        assert args[1] == "claim.pdf"

    def test_missing_row_exits_with_batch_runner_guidance(self, tmp_path, capsys):
        """Missing processed_documents row → exit 1 with actionable guidance
        (run the Docling batch first)."""
        pdf = tmp_path / "claim.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        session = MagicMock()
        factory = MagicMock(return_value=session)

        with patch(
            "src.shared.database.get_session_factory", return_value=factory
        ), patch(
            "src.discovery.database.get_document_by_path", MagicMock(return_value=None)
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_eval_mocked(
                    _base_argv(
                        tmp_path, ["--doc-file", str(pdf), "--from-processed-doc"]
                    ),
                    AsyncMock(),
                )

        assert exc_info.value.code == 1
        # The owned read-only lookup session is closed even on the error path.
        session.close.assert_called_once()
        err = capsys.readouterr().err
        assert "src.discovery.batch_runner" in err

    def test_empty_extracted_text_treated_as_missing(self, tmp_path):
        """A FAILED Docling row (empty extracted_text) → exit 1, not an
        extraction over the empty string."""
        pdf = tmp_path / "claim.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        processed = MagicMock()
        processed.extracted_text = "   "

        with patch(
            "src.shared.database.get_session_factory",
            return_value=MagicMock(return_value=MagicMock()),
        ), patch(
            "src.discovery.database.get_document_by_path",
            MagicMock(return_value=processed),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_eval_mocked(
                    _base_argv(
                        tmp_path, ["--doc-file", str(pdf), "--from-processed-doc"]
                    ),
                    AsyncMock(),
                )
        assert exc_info.value.code == 1

    def test_from_processed_doc_requires_doc_file(self, tmp_path):
        """--from-processed-doc without --doc-file → exit 1 (the resolved
        --doc-file path IS the lookup key)."""
        with pytest.raises(SystemExit) as exc_info:
            _run_eval_mocked(
                _base_argv(tmp_path, ["--from-processed-doc"]), AsyncMock()
            )
        assert exc_info.value.code == 1

    def test_persist_reuses_session_for_lookup(self, tmp_path):
        """With --persist the lookup reuses the persist session (one factory
        call, no second session) and the session stays open for the
        extraction commit."""
        pdf = tmp_path / "claim.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        session = MagicMock()
        factory = MagicMock(return_value=session)
        processed = MagicMock()
        processed.extracted_text = "text from docling"
        lookup = MagicMock(return_value=processed)
        extract = AsyncMock(return_value=_make_batch())

        with patch(
            "src.shared.database.get_session_factory", return_value=factory
        ), patch("src.discovery.database.get_document_by_path", lookup):
            _run_eval_mocked(
                _base_argv(
                    tmp_path,
                    ["--doc-file", str(pdf), "--from-processed-doc", "--persist"],
                ),
                extract,
            )

        assert factory.call_count == 1
        lookup.assert_called_once_with(session, str(pdf.resolve()))
        _, kwargs = extract.call_args
        assert kwargs["session"] is session
        session.commit.assert_called_once()
        session.close.assert_called_once()


class TestSubprocessMetricsInit:
    def test_main_calls_init_subprocess_metrics(self, tmp_path, monkeypatch):
        """F-0023 / ISS-0041: main() mirrors OTel counters into the
        Prometheus multiproc dir before running (extraction_bridge pattern)."""
        schema = tmp_path / "schema.json"
        schema.write_text("{}")
        monkeypatch.setattr(
            _sys, "argv", ["eval_checkpoint", "--schema", str(schema)]
        )
        init_mock = MagicMock()
        with patch(
            "src.analytics.subprocess_metrics.init_subprocess_metrics",
            init_mock,
        ), patch("src.extraction.eval_checkpoint.run_eval", AsyncMock()):
            main()
        init_mock.assert_called_once()
