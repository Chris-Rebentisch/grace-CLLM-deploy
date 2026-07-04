"""Context reinstatement: document corpus summary for CQ priming."""

import re
from collections import Counter

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.discovery.cq_templates import get_templates_for_domain
from src.discovery.database import ProcessedDocumentRow
from src.discovery.models import ProcessingStatus

# Common English stopwords for key term extraction
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "is", "it", "its", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "can", "could", "this", "that", "these", "those",
    "i", "you", "he", "she", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "our", "their", "not", "no", "nor", "so", "if", "then",
    "than", "too", "very", "just", "about", "above", "after", "again", "all",
    "also", "am", "any", "as", "because", "before", "between", "both", "each",
    "few", "further", "get", "got", "here", "how", "into", "more", "most",
    "new", "now", "only", "other", "out", "over", "own", "same", "some", "such",
    "there", "through", "under", "until", "up", "what", "when", "where", "which",
    "while", "who", "whom", "why", "down", "during", "every", "first", "last",
    "many", "much", "must", "never", "next", "once", "per", "re", "still",
    "since", "take", "two", "three", "four", "five", "well", "back", "even",
    "make", "made", "like", "long", "look", "many", "one", "see", "way",
})


def extract_key_terms(text: str, top_n: int = 10) -> list[str]:
    """Extract the top N most frequent meaningful terms from text.

    Removes common English stopwords. Uses simple word frequency counting.
    Returns terms sorted by frequency descending.
    """
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    filtered = [w for w in words if w not in _STOPWORDS]
    counts = Counter(filtered)
    return [term for term, _ in counts.most_common(top_n)]


def generate_context_summary(db: Session) -> dict:
    """Generate a summary of the processed document corpus for CQ priming.

    Returns document counts, per-domain breakdown, business areas with
    suggested CQ focus, and a CTA prompt.
    """
    # Get completed documents
    rows = (
        db.query(ProcessedDocumentRow)
        .filter(ProcessedDocumentRow.status == ProcessingStatus.COMPLETE.value)
        .all()
    )

    total_documents = len(rows)
    total_words = sum(r.word_count or 0 for r in rows)

    # Group by domain
    domains: dict[str, dict] = {}
    for row in rows:
        domain = row.domain or "other"
        if domain not in domains:
            domains[domain] = {"count": 0, "sample_titles": [], "word_count": 0, "texts": []}
        domains[domain]["count"] += 1
        domains[domain]["word_count"] += row.word_count or 0
        if len(domains[domain]["sample_titles"]) < 5:
            domains[domain]["sample_titles"].append(row.file_name)
        if row.extracted_text:
            domains[domain]["texts"].append(row.extracted_text[:500])

    # Build business_areas
    business_areas = []
    for domain, info in sorted(domains.items(), key=lambda x: x[1]["count"], reverse=True):
        combined_text = " ".join(info["texts"])
        key_terms = extract_key_terms(combined_text, top_n=5)

        suggested_focus = ""
        if key_terms:
            suggested_focus = f"Key topics: {', '.join(key_terms)}. What questions do you need answered about your {domain} documents?"

        business_areas.append({
            "domain": domain,
            "document_count": info["count"],
            "word_count": info["word_count"],
            "sample_topics": key_terms,
            "suggested_cq_focus": suggested_focus,
        })

    # Clean domains dict for output (remove internal 'texts' key)
    domains_output = {
        d: {"count": info["count"], "sample_titles": info["sample_titles"], "word_count": info["word_count"]}
        for d, info in domains.items()
    }

    domain_count = len(domains)
    if domain_count == 0:
        cta_prompt = "No documents processed yet. Process your documents first, then come back to author CQs."
    elif domain_count == 1:
        cta_prompt = f"Your documents cover 1 business area. Let's focus on that area."
    else:
        cta_prompt = f"Your documents span {domain_count} business areas. We'll work through each one. Start with the area you know best."

    return {
        "total_documents": total_documents,
        "total_words": total_words,
        "domains": domains_output,
        "business_areas": business_areas,
        "cta_prompt": cta_prompt,
    }


def generate_domain_context(db: Session, domain: str) -> dict:
    """Generate detailed context for a single domain to support per-domain CQ authoring.

    Returns documents, key terms, suggested templates, and a priming prompt.
    """
    rows = (
        db.query(ProcessedDocumentRow)
        .filter(
            ProcessedDocumentRow.status == ProcessingStatus.COMPLETE.value,
            ProcessedDocumentRow.domain == domain,
        )
        .all()
    )

    documents = []
    all_text = []
    for row in rows:
        snippet = (row.extracted_text or "")[:200]
        documents.append({
            "id": str(row.id),
            "file_name": row.file_name,
            "word_count": row.word_count or 0,
            "snippet": snippet,
        })
        if row.extracted_text:
            all_text.append(row.extracted_text)

    combined_text = " ".join(all_text)
    key_terms = extract_key_terms(combined_text, top_n=10)

    suggested = get_templates_for_domain(domain)
    suggested_template_ids = [t.id for t in suggested[:5]]

    doc_count = len(documents)
    if key_terms:
        terms_str = ", ".join(key_terms[:5])
        prompt = f"You have {doc_count} document{'s' if doc_count != 1 else ''} about {domain}. Here are some terms we found: {terms_str}. What questions do you need answered about your {domain} situation?"
    else:
        prompt = f"You have {doc_count} document{'s' if doc_count != 1 else ''} about {domain}. What questions do you need answered?"

    return {
        "domain": domain,
        "document_count": doc_count,
        "documents": documents,
        "key_terms": key_terms,
        "suggested_templates": suggested_template_ids,
        "prompt": prompt,
    }
