"""Groups documents by domain and builds per-domain context digests."""

from uuid import UUID

import structlog
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.discovery.cq_context import extract_key_terms, generate_domain_context
from src.discovery.database import list_documents
from src.discovery.models import ProcessingStatus, load_discovery_config
from src.shared.database import get_db

logger = structlog.get_logger()


class DomainBatch(BaseModel):
    """A group of documents for a single domain, ready for CQ generation."""

    domain: str = Field(description="Domain category")
    document_ids: list[UUID] = Field(description="ProcessedDocument UUIDs")
    document_count: int = Field(description="Number of documents")
    total_words: int = Field(description="Total word count across documents")
    context_digest: str = Field(description="Context summary for prompt injection")
    key_terms: list[str] = Field(description="Top terms from document text")
    sample_excerpts: list[str] = Field(description="First N chars from representative docs")


class DocumentGroup(BaseModel):
    """A small slice of a domain's documents for one CQ generation call.

    Per-document batching (vs the single top-10-longest concatenation) is what
    guarantees every document contributes CQs. Without it, the generator drops all
    but the 10 longest docs and the model anchors on the largest two — see
    `docs/cq-generation-benchmark-25docs.md`.
    """

    label: str = Field(description="Human-readable group label, e.g. 'group 2/7'")
    document_count: int = Field(description="Documents in this group")
    document_names: list[str] = Field(description="File names included in the group")
    text: str = Field(description="Concatenated (separator-delimited) document text")


def _get_cq_gen_config() -> dict:
    """Get cq_generation config from discovery.yaml."""
    config = load_discovery_config()
    return config.get("cq_generation", {})


def build_domain_batches(db: Session | None = None) -> list[DomainBatch]:
    """Query all COMPLETE documents, group by domain, build context digests.

    Returns list of DomainBatch sorted by document_count descending.
    Domains with 0 documents are excluded.
    """
    close_db = False
    if db is None:
        db_gen = get_db()
        db = next(db_gen)
        close_db = True

    gen_config = _get_cq_gen_config()
    max_excerpts = min(gen_config.get("max_sample_documents_per_domain", 10), 5)
    excerpt_chars = gen_config.get("max_sample_excerpt_chars", 500)

    try:
        # Get all completed documents
        all_docs = list_documents(db, status=ProcessingStatus.COMPLETE, limit=10000)

        # Group by domain
        by_domain: dict[str, list] = {}
        for doc in all_docs:
            domain = doc.domain or "other"
            by_domain.setdefault(domain, []).append(doc)

        batches = []
        for domain, docs in by_domain.items():
            if not docs:
                continue

            # Sort by word count descending for sampling
            docs_sorted = sorted(docs, key=lambda d: d.word_count, reverse=True)

            document_ids = [doc.id for doc in docs]
            total_words = sum(doc.word_count for doc in docs)

            # Context digest from cq_context.py
            domain_ctx = generate_domain_context(db, domain)
            context_digest = domain_ctx.get("prompt", "")

            # Key terms from combined text
            combined_text = " ".join(doc.extracted_text for doc in docs if doc.extracted_text)
            key_terms = extract_key_terms(combined_text, top_n=10)

            # Sample excerpts from top docs
            sample_excerpts = []
            for doc in docs_sorted[:max_excerpts]:
                if doc.extracted_text:
                    sample_excerpts.append(doc.extracted_text[:excerpt_chars])

            batches.append(DomainBatch(
                domain=domain,
                document_ids=document_ids,
                document_count=len(docs),
                total_words=total_words,
                context_digest=context_digest,
                key_terms=key_terms,
                sample_excerpts=sample_excerpts,
            ))

        batches.sort(key=lambda b: b.document_count, reverse=True)
        return batches

    finally:
        if close_db:
            try:
                next(db_gen)
            except StopIteration:
                pass


def build_document_groups(
    db: Session,
    domain: str,
    docs_per_group: int | None = None,
    max_chars_per_group: int | None = None,
) -> list[DocumentGroup]:
    """Split a domain's documents into small, fully-covering groups.

    Unlike `build_document_text_for_prompt` (top-10-longest, single concatenation),
    this covers EVERY completed document in the domain. Documents are sorted by word
    count descending and distributed round-robin across the groups, so the large
    documents are spread out rather than piling into one group — that keeps any single
    generation call from being dominated by one or two giant contracts (the failure
    mode in `docs/cq-generation-benchmark-25docs.md`).

    Returns groups in label order ("group 1/N" … "group N/N"). Empty domain → [].
    """
    gen_config = _get_cq_gen_config()
    if docs_per_group is None:
        docs_per_group = gen_config.get("documents_per_batch", 4)
    docs_per_group = max(1, int(docs_per_group))
    if max_chars_per_group is None:
        max_chars_per_group = gen_config.get("max_document_chars_per_batch", 200000)

    docs = list_documents(db, status=ProcessingStatus.COMPLETE, domain=domain, limit=10000)
    docs = [d for d in docs if (d.extracted_text or "").strip()]
    if not docs:
        return []

    docs_sorted = sorted(docs, key=lambda d: d.word_count, reverse=True)

    # Number of groups so each holds ~docs_per_group docs; round-robin spreads the
    # largest documents across distinct groups instead of clustering them.
    n_groups = max(1, (len(docs_sorted) + docs_per_group - 1) // docs_per_group)
    buckets: list[list] = [[] for _ in range(n_groups)]
    for i, doc in enumerate(docs_sorted):
        buckets[i % n_groups].append(doc)

    groups: list[DocumentGroup] = []
    for idx, bucket in enumerate(buckets, start=1):
        if not bucket:
            continue
        # Within a group, keep documents in a stable name order for reproducibility.
        bucket = sorted(bucket, key=lambda d: d.file_name or "")
        parts: list[str] = []
        total = 0
        for doc in bucket:
            separator = f"\n--- Document: {doc.file_name} (domain: {domain}) ---\n"
            chunk = separator + (doc.extracted_text or "")
            if total + len(chunk) > max_chars_per_group:
                remaining = max_chars_per_group - total
                if remaining > len(separator) + 100:
                    parts.append(chunk[:remaining])
                break
            parts.append(chunk)
            total += len(chunk)
        groups.append(DocumentGroup(
            label=f"group {idx}/{n_groups}",
            document_count=len(bucket),
            document_names=[d.file_name for d in bucket],
            text="".join(parts),
        ))

    logger.info(
        "document_groups_built",
        domain=domain,
        documents=len(docs_sorted),
        groups=len(groups),
        docs_per_group=docs_per_group,
    )
    return groups


def build_document_text_for_prompt(
    db: Session,
    domain: str,
    max_chars: int | None = None,
) -> str:
    """Build concatenated document text for a single domain, truncated to max_chars.

    Selects up to 10 documents (prioritize longest), concatenates with separators,
    truncates at max_chars.
    """
    if max_chars is None:
        gen_config = _get_cq_gen_config()
        max_chars = gen_config.get("max_document_chars_per_batch", 12000)

    docs = list_documents(db, status=ProcessingStatus.COMPLETE, domain=domain, limit=10000)
    docs_sorted = sorted(docs, key=lambda d: d.word_count, reverse=True)[:10]

    parts = []
    total_chars = 0
    included = 0

    for doc in docs_sorted:
        separator = f"\n--- Document: {doc.file_name} (domain: {domain}) ---\n"
        text = doc.extracted_text or ""
        chunk = separator + text

        if total_chars + len(chunk) > max_chars:
            remaining = max_chars - total_chars
            if remaining > len(separator) + 100:
                parts.append(chunk[:remaining])
                included += 1
            remaining_docs = len(docs_sorted) - included
            if remaining_docs > 0:
                parts.append(f"\n... [truncated, {remaining_docs} more documents]")
            break

        parts.append(chunk)
        total_chars += len(chunk)
        included += 1

    return "".join(parts)


def _sample_doc_text(text: str, budget: int) -> str:
    """Return up to ``budget`` chars of ``text``, sampled to span the document.

    Schema/ontology extraction needs the *variety* of types and relationships a
    document defines, and those are spread across the whole contract (parties and
    definitions up front, obligations in the body, governing-law/indemnity near
    the end). When a doc is longer than its budget we therefore take three
    windows — head, middle, tail — instead of only the head, so a long contract
    still contributes its mid- and end-clause concepts.
    """
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    win = max(1, budget // 3)
    head = text[:win]
    mid_start = max(0, (len(text) - win) // 2)
    mid = text[mid_start : mid_start + win]
    tail = text[len(text) - win :]
    return f"{head}\n […] \n{mid}\n […] \n{tail}"


def build_balanced_document_text(
    db: Session,
    domain: str,
    max_chars: int | None = None,
) -> str:
    """Concatenate text from EVERY document in a domain within a char budget.

    Unlike ``build_document_text_for_prompt`` (top-10-longest, the first docs
    fill the budget and the rest are dropped), this gives every completed
    document an equal share of ``max_chars`` so all of them contribute to the
    ontology proposal — the same all-documents-contribute guarantee that
    ``build_document_groups`` provides for CQ generation, but as a single
    balanced prompt so the gpt-oss call count is unchanged. Long docs are
    head/middle/tail-sampled (``_sample_doc_text``) to fit their share.
    """
    if max_chars is None:
        gen_config = _get_cq_gen_config()
        max_chars = gen_config.get("max_document_chars_per_batch", 12000)

    docs = list_documents(
        db, status=ProcessingStatus.COMPLETE, domain=domain, limit=10000
    )
    if not docs:
        return ""

    # Longest first so any rounding slack from short docs flows to docs that can
    # use it; every doc still gets at least its equal per-doc share.
    docs_sorted = sorted(docs, key=lambda d: d.word_count, reverse=True)
    per_doc = max(1, max_chars // len(docs_sorted))

    parts: list[str] = []
    for doc in docs_sorted:
        separator = f"\n--- Document: {doc.file_name} (domain: {domain}) ---\n"
        budget = per_doc - len(separator)
        body = _sample_doc_text(doc.extracted_text or "", budget)
        parts.append(separator + body)

    return "".join(parts)
