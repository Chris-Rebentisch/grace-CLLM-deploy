"""Rule-based metadata tagger: assigns project and domain from file paths."""

import re
from pathlib import Path

from src.discovery.models import ProcessedDocument, load_discovery_config

# ---------------------------------------------------------------------------
# Domain vocabularies (F-0006 / ISS-0052, validation run 2026-07-03).
#
# Capture-the-why: assign_domain() previously matched ONLY the literal
# category name against the file path — a lease that didn't live in a
# directory named "real_estate" bucketed as "other" (16/20 clearly-domained
# corpus docs did). The algorithm stays vocab-driven; these vocabularies
# make it honest by mapping characteristic document terms to the EXISTING
# domain_categories in config/discovery.yaml. Finance-instrument terms
# (portfolio, securities, term sheet, credit facility, ...) map to
# corporate_structure — the closest existing category (capital/holdings
# structure); there is deliberately no new category.
#
# Scoring: multi-word phrases are strong evidence (weight 2), single words
# weak (weight 1). A domain needs a total score >= 2 to beat "other", so one
# stray generic word never flips a genuinely unclassifiable document.
# ---------------------------------------------------------------------------

DOMAIN_VOCABULARIES: dict[str, list[str]] = {
    "corporate_structure": [
        "operating agreement",
        "articles of organization",
        "articles of incorporation",
        "certificate of formation",
        "holding company",
        "board of directors",
        "member interest",
        "membership interest",
        "shareholder",
        "bylaws",
        "resolution",
        "llc",
        "subsidiary",
        "trust agreement",
        "trustee",
        "estate plan",
        "beneficiary",
        # Finance instruments — mapped here (capital/holdings structure).
        "term sheet",
        "credit facility",
        "promissory note",
        "loan agreement",
        "covenant",
        "portfolio",
        "holdings",
        "securities",
        "brokerage",
    ],
    "real_estate": [
        "lease",
        "landlord",
        "tenant",
        "zoning",
        "parcel",
        "easement",
        "deed",
        "mortgage",
        "premises",
        "property management",
        "escrow",
        "appraisal",
        "rent",
        "real property",
        "survey",
    ],
    "legal": [
        "attorney",
        "counsel",
        "litigation",
        "plaintiff",
        "defendant",
        "settlement agreement",
        "indemnification",
        "governing law",
        "arbitration",
        "power of attorney",
        "privileged and confidential",
        "legal opinion",
    ],
    "tax": [
        "tax return",
        "taxable",
        "deduction",
        "withholding",
        "irs",
        "k-1",
        "1099",
        "w-2",
        "cpa",
        "estimated tax",
    ],
    "insurance": [
        "policy",
        "premium",
        "coverage",
        "insurer",
        "insured",
        "underwriting",
        "deductible",
        "claim number",
        "certificate of insurance",
    ],
    "operations": [
        "standard operating procedure",
        "maintenance",
        "logistics",
        "inventory",
        "work order",
        "facilities",
    ],
    "vendors": [
        "vendor",
        "supplier",
        "purchase order",
        "statement of work",
        "invoice",
        "contractor",
    ],
    "hr": [
        "employee",
        "employment agreement",
        "offer letter",
        "payroll",
        "onboarding",
        "termination",
        "benefits enrollment",
    ],
}

# Minimum vocabulary score before a domain beats "other".
_VOCAB_SCORE_FLOOR = 2

# Cap content scanning for cheapness on very large documents.
_TEXT_SCAN_CHARS = 20_000


def assign_project(file_path: Path, config: dict) -> str:
    """Assign a project label based on file path using project_directory_mappings.

    Checks if any key in project_directory_mappings appears as a directory
    component in the file path (case-insensitive). Returns the mapped project
    name, or empty string if no match.
    """
    mappings = config.get("project_directory_mappings") or {}
    if not mappings:
        return ""

    path_parts_lower = [p.lower() for p in file_path.parts]
    for key, project_name in mappings.items():
        if key.lower() in path_parts_lower:
            return project_name
    return ""


def _vocab_score(domain: str, haystack: str) -> int:
    """Score one domain's vocabulary against lowercase text.

    Multi-word phrases count 2 per distinct phrase present; single words
    count 1 per distinct word present (word-boundary match, so "policy"
    doesn't fire inside "policyholder-agnostic path names" incorrectly).
    """
    score = 0
    for term in DOMAIN_VOCABULARIES.get(domain, []):
        if " " in term:
            if term in haystack:
                score += 2
        else:
            if re.search(rf"\b{re.escape(term)}\b", haystack):
                score += 1
    return score


def assign_domain(file_path: Path, config: dict, text: str = "") -> str:
    """Assign a domain category based on file path and document content.

    Two vocab-driven passes (F-0006 / ISS-0052):

    1. Path match (original behavior, highest priority): a domain_categories
       name appearing in the file path wins immediately.
    2. Content vocabulary match: DOMAIN_VOCABULARIES terms are scored against
       the file name + document text; the highest-scoring domain wins when
       its score reaches the floor. Ties resolve in domain_categories order.

    Genuinely unclassifiable documents still land in "other".
    """
    domains = config.get("domain_categories", [])
    # Pass 1 — path components (original behavior).
    path_str = str(file_path).lower()
    for domain in domains:
        if domain == "other":
            continue
        if domain.lower() in path_str:
            return domain

    # Pass 2 — content vocabulary (only for domains the config declares).
    haystack = (file_path.name + "\n" + (text or "")[:_TEXT_SCAN_CHARS]).lower()
    best_domain = "other"
    best_score = 0
    for domain in domains:
        if domain == "other":
            continue
        score = _vocab_score(domain, haystack)
        if score > best_score:
            best_domain = domain
            best_score = score
    if best_score >= _VOCAB_SCORE_FLOOR:
        return best_domain
    return "other"


def tag_document(doc: ProcessedDocument, file_path: Path) -> ProcessedDocument:
    """Apply project and domain tags to a ProcessedDocument. Returns updated doc."""
    config = load_discovery_config()
    project = assign_project(file_path, config)
    # F-0006 / ISS-0052: pass extracted text so content vocabulary can
    # classify documents whose paths carry no domain hint.
    domain = assign_domain(file_path, config, text=doc.extracted_text or "")
    return doc.model_copy(update={"project": project, "domain": domain})
