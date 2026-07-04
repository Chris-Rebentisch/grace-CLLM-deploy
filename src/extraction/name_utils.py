"""Shared name normalization for entity dedup and resolution.

Single source of truth for name normalization logic used by both
ExtractionPipeline cross-chunk dedup and EntityResolver Tier 1.
"""

DEFAULT_STRIP_SUFFIXES: list[str] = [
    "llc", "inc.", "inc", "corp.", "corp", "ltd.", "ltd",
    "l.l.c.", "llp", "l.p.", "lp", "plc", "gmbh", "s.a.", "s.a", "n.v.", "n.v",
]

# F-0025b / ISS-0035 (2026-07-03) capture-the-why: OCR scans misread corporate
# suffix tokens (LLC -> LLO/LLG, Inc -> 1nc/lnc, ...). "Cedar Grove Residences
# LLO" minted a phantom near-duplicate of "Cedar Grove Residences LLC" because
# "llo" was not a strippable suffix, so normalization never converged the two
# names. Each key maps a misread trailing token to its canonical suffix.
# Variants are stripped ONLY at a token boundary (see _ends_with_suffix_token)
# so legitimate names ending in these letter sequences ("Apollo", "Costello")
# are never mutilated — conservative bias per the ER false-merge precedent.
SUFFIX_OCR_VARIANTS: dict[str, str] = {
    "llo": "llc",   # C -> O scan misread
    "llg": "llc",   # C -> G scan misread
    "l.l.c": "llc",  # trailing-period loss ("L.L.C" without final dot)
    "lnc": "inc",   # I -> l scan misread
    "1nc": "inc",   # I -> 1 scan misread
    "c0rp": "corp",  # o -> 0 scan misread
    "1td": "ltd",   # l -> 1 scan misread
}


def _ends_with_suffix_token(normalized: str, suffix: str) -> bool:
    """True when ``normalized`` ends with ``suffix`` as a whole trailing token.

    F-0025b / ISS-0035 capture-the-why: the previous bare ``endswith()`` check
    was substring-based, which stripped suffix letter-sequences out of real
    names (and would turn "Apollo" into "apo" once the OCR variant "llo"
    joined the strip list). Require a space/comma boundary before the suffix,
    and never strip a name that IS the suffix.
    """
    if len(normalized) <= len(suffix) or not normalized.endswith(suffix):
        return False
    return normalized[-len(suffix) - 1] in (" ", ",")


def normalize_entity_name(name: str, suffixes: list[str] | None = None) -> str:
    """Normalize entity name: lowercase, strip, remove corporate suffixes.

    Used by both cross-chunk dedup (ExtractionPipeline._dedup_entities)
    and entity resolution (EntityResolver Tier 1). Single source of truth
    for name normalization logic.

    OCR-misread suffix variants (SUFFIX_OCR_VARIANTS) strip like their
    canonical suffix — but only when the canonical form is present in the
    active suffix list, so custom suffix lists opt out automatically
    (F-0025b / ISS-0035).

    Documented limitation: does not handle synonym/abbreviation pairs
    (Corp vs Corporation). v1 only.

    Args:
        name: Raw entity name.
        suffixes: Optional custom suffix list. Defaults to DEFAULT_STRIP_SUFFIXES.

    Returns:
        Normalized name string.
    """
    if suffixes is None:
        suffixes = DEFAULT_STRIP_SUFFIXES
    normalized = name.lower().strip()
    # Remove trailing punctuation before suffix check
    normalized = normalized.rstrip(",.")
    candidates = list(suffixes) + [
        variant
        for variant, canonical in SUFFIX_OCR_VARIANTS.items()
        if canonical in suffixes
    ]
    for suffix in candidates:
        if _ends_with_suffix_token(normalized, suffix):
            normalized = normalized[: -len(suffix)].rstrip(" ,")
            break  # Only strip one suffix
    return normalized.strip()


def expand_suffix_ocr_variants(name: str) -> list[str]:
    """Return canonical-suffix respellings of ``name`` when its trailing token
    is a known OCR misread of a corporate suffix.

    "Cedar Grove Residences LLO" -> ["Cedar Grove Residences LLC"].
    Returns [] when the trailing token is not a known variant.

    F-0025b / ISS-0035: used by EntityResolver to give suffix-misread names a
    shot at candidacy against the canonically-suffixed entity already in the
    graph. Matches are routed to Tier-3 adjudication — never silently merged.
    """
    stripped = name.strip()
    parts = stripped.rsplit(None, 1)
    if len(parts) != 2:
        return []
    prefix, token = parts
    key = token.lower().rstrip(",.")
    canonical = SUFFIX_OCR_VARIANTS.get(key)
    if canonical is None:
        return []
    rendered = canonical.upper() if token.isupper() else canonical
    return [f"{prefix} {rendered}"]
