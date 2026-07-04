"""Tests for shared name normalization utility."""

from src.extraction.name_utils import (
    DEFAULT_STRIP_SUFFIXES,
    SUFFIX_OCR_VARIANTS,
    expand_suffix_ocr_variants,
    normalize_entity_name,
)


def test_lowercase_and_strip():
    """normalize_entity_name lowercases and strips whitespace."""
    assert normalize_entity_name("  Acme Corp  ") == "acme"


def test_suffix_stripping():
    """Corporate suffix is stripped (e.g., LLC)."""
    assert normalize_entity_name("Acme Corp LLC") == "acme corp"


def test_only_first_suffix_stripped():
    """Only the first matching suffix is stripped."""
    # "ltd" matches first, inner "inc" is untouched
    assert normalize_entity_name("Acme Inc Ltd") == "acme inc"


def test_custom_suffix_list():
    """Custom suffix list overrides defaults."""
    result = normalize_entity_name("Acme Holding", suffixes=["holding"])
    assert result == "acme"


def test_no_suffix_match():
    """When no suffix matches, name is still cleaned."""
    result = normalize_entity_name("  GlobalTech Solutions  ")
    assert result == "globaltech solutions"


def test_new_suffixes_present():
    """New suffixes added in Chunk 20 (llp, plc, gmbh, s.a., n.v.) work."""
    assert normalize_entity_name("Deutsche Bank GmbH") == "deutsche bank"
    assert normalize_entity_name("Shell PLC") == "shell"
    assert normalize_entity_name("Baker McKenzie LLP") == "baker mckenzie"
    assert normalize_entity_name("TotalEnergies S.A.") == "totalenergies"
    assert normalize_entity_name("Unilever N.V.") == "unilever"


def test_default_suffixes_include_new_entries():
    """DEFAULT_STRIP_SUFFIXES includes all expected entries."""
    for suffix in ["llp", "plc", "gmbh", "s.a.", "n.v."]:
        assert suffix in DEFAULT_STRIP_SUFFIXES


# --- F-0025b / ISS-0035: OCR suffix variants ---


def test_ocr_variant_llo_normalizes_like_llc():
    """LLO (scan misread of LLC) strips so both forms converge (F-0025b)."""
    assert (
        normalize_entity_name("Cedar Grove Residences LLO")
        == normalize_entity_name("Cedar Grove Residences LLC")
        == "cedar grove residences"
    )


def test_ocr_variant_l_dot_l_dot_c_normalizes():
    """L.L.C (trailing-period loss) strips like L.L.C. (F-0025b)."""
    assert normalize_entity_name("Acme L.L.C") == "acme"
    assert normalize_entity_name("Acme L.L.C.") == "acme"


def test_ocr_variants_inc_and_ltd():
    """1nc / lnc / 1td misreads strip like their canonical suffixes."""
    assert normalize_entity_name("Acme 1nc") == "acme"
    assert normalize_entity_name("Acme lnc") == "acme"
    assert normalize_entity_name("Acme 1td") == "acme"


def test_token_boundary_protects_real_names():
    """Suffix stripping requires a token boundary — 'Apollo' is not 'Apo LLO'
    and 'Zinc' is not 'Z Inc' (F-0025b conservatism guard)."""
    assert normalize_entity_name("Apollo") == "apollo"
    assert normalize_entity_name("Costello") == "costello"
    assert normalize_entity_name("Zinc") == "zinc"
    # Multi-token name whose last token merely ENDS in a suffix string
    assert normalize_entity_name("Bella Costello") == "bella costello"


def test_name_that_is_only_a_suffix_is_untouched():
    """A name that IS a suffix token is never stripped to empty."""
    assert normalize_entity_name("LLC") == "llc"


def test_custom_suffix_list_opts_out_of_ocr_variants():
    """OCR variants apply only when their canonical form is in the list."""
    # 'llc' not in the custom list -> 'llo' variant must not strip
    assert normalize_entity_name("Acme LLO", suffixes=["holding"]) == "acme llo"


def test_expand_suffix_ocr_variants_llo():
    """LLO trailing token expands to the canonical LLC respelling."""
    assert expand_suffix_ocr_variants("Cedar Grove Residences LLO") == [
        "Cedar Grove Residences LLC"
    ]


def test_expand_suffix_ocr_variants_no_variant():
    """Canonical or non-suffix trailing tokens expand to nothing."""
    assert expand_suffix_ocr_variants("Cedar Grove Residences LLC") == []
    assert expand_suffix_ocr_variants("GlobalTech Solutions") == []
    assert expand_suffix_ocr_variants("Apollo") == []  # single token
    assert expand_suffix_ocr_variants("Theodore") == []


def test_expand_suffix_ocr_variants_case_and_punctuation():
    """Expansion handles trailing punctuation and preserves suffix casing."""
    # mixed-case token renders lowercase canonical; trailing '.' tolerated
    assert expand_suffix_ocr_variants("Acme 1nc.") == ["Acme inc"]
    # uppercase variant token renders uppercase canonical
    assert expand_suffix_ocr_variants("Acme LLG") == ["Acme LLC"]
    # lowercase variant token renders lowercase canonical
    assert expand_suffix_ocr_variants("acme llo") == ["acme llc"]


def test_all_variant_canonicals_are_strippable():
    """Every OCR-variant canonical form is in DEFAULT_STRIP_SUFFIXES,
    so variant stripping is always active on the default path."""
    for canonical in SUFFIX_OCR_VARIANTS.values():
        assert canonical in DEFAULT_STRIP_SUFFIXES
