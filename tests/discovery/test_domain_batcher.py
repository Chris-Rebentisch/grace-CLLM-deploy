"""Tests for domain batcher."""

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from src.discovery.database import create_document
from src.discovery.domain_batcher import (
    build_balanced_document_text,
    build_document_groups,
    build_document_text_for_prompt,
    build_domain_batches,
    _sample_doc_text,
)
from src.discovery.models import FileType, ProcessedDocument, ProcessingStatus
from src.shared.database import get_engine


# D485 — SAVEPOINT-rollback fixture (Chunk 75a).
# Invariant: replaces TRUNCATE-based per-test isolation with SQLAlchemy 2.0
# SAVEPOINT-rollback pattern. Authorization: D485 / spec §6 Step 2.


@pytest.fixture()
def db_session():
    """Yield a SAVEPOINT-rollback session for testing (D485)."""
    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(text("TRUNCATE competency_questions, cq_clusters, processed_documents CASCADE"))
    session = Session(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


def _insert_doc(db, path, domain="other", text_content="sample text", word_count=100):
    doc = ProcessedDocument(
        file_path=path,
        file_name=path.split("/")[-1],
        file_type=FileType.PDF,
        file_size_bytes=1024,
        domain=domain,
        word_count=word_count,
        extracted_text=text_content,
        status=ProcessingStatus.COMPLETE,
    )
    return create_document(db, doc)


def test_build_domain_batches(db_session):
    """Insert 10 docs across 3 domains, verify 3 batches."""
    for i in range(4):
        _insert_doc(db_session, f"/tmp/ins_{i}.pdf", "insurance", f"insurance doc {i}")
    for i in range(3):
        _insert_doc(db_session, f"/tmp/leg_{i}.pdf", "legal", f"legal doc {i}")
    for i in range(3):
        _insert_doc(db_session, f"/tmp/tax_{i}.pdf", "tax", f"tax doc {i}")

    batches = build_domain_batches(db_session)
    assert len(batches) == 3
    # Sorted by document_count descending
    assert batches[0].document_count == 4
    assert batches[0].domain == "insurance"
    for b in batches:
        assert b.context_digest
        assert len(b.document_ids) == b.document_count


def test_build_domain_batches_excludes_empty(db_session):
    """Verify domains with 0 docs are excluded."""
    _insert_doc(db_session, "/tmp/doc.pdf", "insurance", "test")

    batches = build_domain_batches(db_session)
    domains = [b.domain for b in batches]
    assert "insurance" in domains
    assert "legal" not in domains


def test_build_document_text_truncation(db_session):
    """Verify text truncated at max_chars."""
    # Insert a doc with lots of text
    long_text = "word " * 5000  # ~25000 chars
    _insert_doc(db_session, "/tmp/long.pdf", "insurance", long_text, word_count=5000)

    result = build_document_text_for_prompt(db_session, "insurance", max_chars=1000)
    assert len(result) <= 1100  # Allow some overhead for separator


def test_build_document_text_separator_format(db_session):
    """Verify document separators in output."""
    _insert_doc(db_session, "/tmp/policy.pdf", "insurance", "Insurance content here")

    result = build_document_text_for_prompt(db_session, "insurance")
    assert "--- Document: policy.pdf (domain: insurance) ---" in result
    assert "Insurance content here" in result


def test_sample_excerpts_limit(db_session):
    """Verify max 5 excerpts per domain."""
    for i in range(8):
        _insert_doc(db_session, f"/tmp/doc_{i}.pdf", "insurance", f"excerpt content {i}")

    batches = build_domain_batches(db_session)
    ins_batch = next(b for b in batches if b.domain == "insurance")
    assert len(ins_batch.sample_excerpts) <= 5


def test_document_groups_cover_every_document(db_session):
    """Per-document batching must include ALL docs — not just the top-10-longest.

    This is the core regression guard for the 25-doc benchmark finding: the legacy
    single-concat path dropped all but 10 docs; build_document_groups covers them all.
    """
    for i in range(25):
        _insert_doc(
            db_session,
            f"/tmp/legal_{i:02d}.pdf",
            "legal",
            f"contract body number {i} " * (i + 1),  # varied lengths
            word_count=(i + 1) * 100,
        )

    groups = build_document_groups(db_session, "legal", docs_per_group=4)

    # Every one of the 25 documents appears in exactly one group.
    covered = [name for g in groups for name in g.document_names]
    assert len(covered) == 25
    assert len(set(covered)) == 25
    expected = {f"legal_{i:02d}.pdf" for i in range(25)}
    assert set(covered) == expected


def test_document_groups_spread_large_docs(db_session):
    """Round-robin distribution keeps the largest docs out of a single group."""
    # 8 docs, descending sizes; docs_per_group=4 -> 2 groups.
    for i in range(8):
        _insert_doc(
            db_session,
            f"/tmp/sz_{i}.pdf",
            "legal",
            "x " * (800 - i * 50),
            word_count=800 - i * 50,
        )

    groups = build_document_groups(db_session, "legal", docs_per_group=4)
    assert len(groups) == 2
    # The two largest docs (sz_0, sz_1) must not land in the same group.
    largest_two = {"sz_0.pdf", "sz_1.pdf"}
    in_group_0 = largest_two & set(groups[0].document_names)
    assert len(in_group_0) == 1, "largest two docs should be split across groups"


def test_document_groups_per_group_char_cap(db_session):
    """Per-group text is capped at max_chars_per_group."""
    long_text = "word " * 6000  # ~30000 chars
    _insert_doc(db_session, "/tmp/big.pdf", "legal", long_text, word_count=6000)

    groups = build_document_groups(
        db_session, "legal", docs_per_group=4, max_chars_per_group=1000
    )
    assert len(groups) == 1
    assert len(groups[0].text) <= 1100  # allow separator overhead


def test_document_groups_empty_domain(db_session):
    """Empty domain returns no groups."""
    assert build_document_groups(db_session, "legal") == []


# --- build_balanced_document_text: all-docs coverage for schema extraction ---


def test_sample_doc_text_short_passthrough():
    """A doc within budget is returned verbatim (no sampling markers)."""
    assert _sample_doc_text("short body", 100) == "short body"


def test_sample_doc_text_spans_head_middle_tail():
    """A long doc is sampled at head, middle, and tail within budget."""
    long = "A" * 300 + "B" * 300 + "C" * 300
    s = _sample_doc_text(long, 90)
    assert s.startswith("A") and "B" in s and s.rstrip().endswith("C")
    # body windows total ~budget (markers add a little); never the whole doc
    assert len(s) < len(long)


def test_balanced_text_includes_every_document(db_session):
    """EVERY doc contributes — not just the 10 longest (the top-10 bug)."""
    for i in range(15):
        # Descending sizes so the legacy top-10 path would drop the smallest 5.
        _insert_doc(
            db_session,
            f"/tmp/bal_{i:02d}.txt",
            "legal",
            text_content=f"CONTRACT-{i:02d} " + ("word " * (500 - i * 10)),
            word_count=500 - i * 10,
        )

    text = build_balanced_document_text(db_session, "legal", max_chars=200_000)

    # All 15 documents appear by filename (top-10 builder would include only 10).
    for i in range(15):
        assert f"bal_{i:02d}.txt" in text
    # Each doc's distinct marker survives — every doc actually contributed body.
    for i in range(15):
        assert f"CONTRACT-{i:02d}" in text


def test_balanced_text_respects_char_budget(db_session):
    """Total stays within ~budget by giving each doc an equal share."""
    for i in range(20):
        _insert_doc(
            db_session,
            f"/tmp/big_{i:02d}.txt",
            "legal",
            text_content="X" * 50_000,  # each far exceeds its per-doc share
            word_count=8000,
        )

    budget = 100_000
    text = build_balanced_document_text(db_session, "legal", max_chars=budget)

    # 20 docs share 100k -> ~5k each; all present, total near (not wildly over) budget.
    for i in range(20):
        assert f"big_{i:02d}.txt" in text
    assert len(text) <= budget * 1.2  # separators + sampling markers add a little


def test_balanced_text_empty_domain(db_session):
    """No docs -> empty string (no crash)."""
    assert build_balanced_document_text(db_session, "legal") == ""
