"""Tests for Discovery metadata tagger."""

from pathlib import Path

from src.discovery.metadata_tagger import assign_domain, assign_project, tag_document
from src.discovery.models import FileType, ProcessedDocument


def _make_doc(file_path: str = "/tmp/test.pdf") -> ProcessedDocument:
    """Create a minimal ProcessedDocument for testing."""
    return ProcessedDocument(
        file_path=file_path,
        file_name=Path(file_path).name,
        file_type=FileType.PDF,
        file_size_bytes=1024,
    )


def test_assign_project_from_path():
    """File in a directory matching project_directory_mappings gets the correct project."""
    config = {
        "project_directory_mappings": {
            "cedar_cay": "Cedar Cay",
            "coconut_grove": "Coconut Grove",
        },
        "domain_categories": ["other"],
    }
    result = assign_project(Path("/data/cedar_cay/contracts/lease.pdf"), config)
    assert result == "Cedar Cay"


def test_assign_project_no_match():
    """File not matching any mapping gets empty string."""
    config = {
        "project_directory_mappings": {
            "cedar_cay": "Cedar Cay",
        },
        "domain_categories": ["other"],
    }
    result = assign_project(Path("/data/random/file.pdf"), config)
    assert result == ""


def test_assign_project_empty_mappings():
    """Empty or missing mappings returns empty string."""
    config = {"project_directory_mappings": {}, "domain_categories": ["other"]}
    result = assign_project(Path("/data/anything/file.pdf"), config)
    assert result == ""


def test_assign_domain_from_path():
    """File in a directory named 'insurance' gets domain='insurance'."""
    config = {
        "domain_categories": [
            "corporate_structure", "real_estate", "insurance", "legal",
            "tax", "operations", "vendors", "hr", "other",
        ],
    }
    result = assign_domain(Path("/data/insurance/policy.pdf"), config)
    assert result == "insurance"


def test_assign_domain_no_match():
    """File with no domain-related path components gets 'other'."""
    config = {
        "domain_categories": [
            "corporate_structure", "insurance", "legal", "other",
        ],
    }
    result = assign_domain(Path("/data/random/stuff/file.pdf"), config)
    assert result == "other"


def test_tag_document_combined():
    """tag_document() applies both project and domain."""
    doc = _make_doc(file_path="/data/insurance/contracts/policy.pdf")
    tagged = tag_document(doc, Path("/data/insurance/contracts/policy.pdf"))
    assert tagged.domain == "insurance"
    # project depends on discovery.yaml mappings (default empty), so just verify it's a string
    assert isinstance(tagged.project, str)


# ---------------------------------------------------------------------------
# F-0006 / ISS-0052 — content-vocabulary domain classification.
# The validation run bucketed 16/20 clearly-domained documents as
# "other" because only the literal category name was matched against the
# file PATH. Representative snippets must now classify by content.
# ---------------------------------------------------------------------------

_ALL_DOMAINS_CONFIG = {
    "domain_categories": [
        "corporate_structure", "real_estate", "insurance", "legal",
        "tax", "operations", "vendors", "hr", "other",
    ],
}

_NEUTRAL_PATH = Path("/data/docs/document-041.pdf")


def test_lease_text_classifies_real_estate():
    text = (
        "This Lease is entered into between the Landlord and the Tenant "
        "for the Premises located at 14 Rosewood Road. Rent is due monthly."
    )
    assert assign_domain(_NEUTRAL_PATH, _ALL_DOMAINS_CONFIG, text) == "real_estate"


def test_operating_agreement_classifies_corporate_structure():
    text = (
        "Amended and Restated Operating Agreement of Meridian Holdings LLC. "
        "Each Member holds a membership interest as set forth in Exhibit A."
    )
    assert (
        assign_domain(_NEUTRAL_PATH, _ALL_DOMAINS_CONFIG, text)
        == "corporate_structure"
    )


def test_portfolio_statement_classifies_corporate_structure():
    text = (
        "Quarterly portfolio statement. Total holdings include equity "
        "securities held in the brokerage account."
    )
    assert (
        assign_domain(_NEUTRAL_PATH, _ALL_DOMAINS_CONFIG, text)
        == "corporate_structure"
    )


def test_zoning_letter_classifies_real_estate():
    text = (
        "Re: zoning determination for parcel 12-045. The easement recorded "
        "against the deed remains in effect."
    )
    assert assign_domain(_NEUTRAL_PATH, _ALL_DOMAINS_CONFIG, text) == "real_estate"


def test_bank_term_sheet_classifies_corporate_structure():
    text = (
        "Indicative term sheet for a senior secured credit facility. "
        "Financial covenant: minimum liquidity of $5m."
    )
    assert (
        assign_domain(_NEUTRAL_PATH, _ALL_DOMAINS_CONFIG, text)
        == "corporate_structure"
    )


def test_settlement_agreement_classifies_legal():
    text = (
        "Settlement Agreement between plaintiff and defendant. Counsel for "
        "each party shall execute per the governing law of the State."
    )
    assert assign_domain(_NEUTRAL_PATH, _ALL_DOMAINS_CONFIG, text) == "legal"


def test_genuinely_unclassifiable_text_stays_other():
    text = (
        "Weekly notes: the weather was pleasant. We discussed the upcoming "
        "birthday party and the new recipe for banana bread."
    )
    assert assign_domain(_NEUTRAL_PATH, _ALL_DOMAINS_CONFIG, text) == "other"


def test_single_stray_keyword_does_not_flip_domain():
    """One weak (single-word) hit stays below the score floor."""
    text = "Please review the attached policy notes from the meeting."
    assert assign_domain(_NEUTRAL_PATH, _ALL_DOMAINS_CONFIG, text) == "other"


def test_path_match_still_wins_over_content():
    """Original path-based behavior remains the highest-priority pass."""
    text = "This Lease between Landlord and Tenant covers the Premises."
    path = Path("/data/tax/lease-tax-treatment.pdf")
    assert assign_domain(path, _ALL_DOMAINS_CONFIG, text) == "tax"


def test_tag_document_passes_extracted_text():
    doc = _make_doc(file_path="/data/docs/agreement-007.pdf")
    doc = doc.model_copy(
        update={
            "extracted_text": (
                "Operating Agreement of Cedar Cay LLC. The Members and the "
                "board of directors adopt these bylaws by resolution."
            )
        }
    )
    tagged = tag_document(doc, Path("/data/docs/agreement-007.pdf"))
    assert tagged.domain == "corporate_structure"
