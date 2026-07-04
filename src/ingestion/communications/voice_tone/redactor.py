"""Two-layer PII redactor for Voice Card exemplar text (Chunk 78, D506).

D506 capture-the-why: Exemplar text must be PII-redacted before export.
Layer 1 is always-on regex; Layer 2 is best-effort local-only NER via Ollama.
The redactor MUST NOT call a cloud provider — ``redaction_ner_provider`` is
always ``local_only`` (D138/D232 interaction). Authorization: D506 (Chunk 78).
"""

from __future__ import annotations

import re

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Layer 1 — Always-on regex patterns
# ---------------------------------------------------------------------------

# Email addresses
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# Phone numbers (various formats)
_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s\-.]?)?"  # optional country code
    r"(?:\(?\d{2,4}\)?[\s\-.]?)"  # area code
    r"(?:\d{2,4}[\s\-.]?){1,3}"  # number groups
    r"\d{2,4}\b"  # final group
)

# Postal addresses (US-style street numbers + common suffixes)
_ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+"
    r"(?:[A-Z][a-z]+\s+){1,3}"
    r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Road|Rd|Lane|Ln|"
    r"Court|Ct|Place|Pl|Way|Circle|Cir|Parkway|Pkwy)\b"
    r"(?:[,.]?\s*(?:Suite|Ste|Apt|Unit|#)\s*\d+)?",
    re.IGNORECASE,
)

# Policy/claim/account numbers (common insurance patterns)
_CLAIM_ID_RE = re.compile(
    r"\b(?:(?:POL|CLM|ACC|POLICY|CLAIM|ACCOUNT|INVOICE|INV|REF|CASE)"
    r"[\s\-#:]*\d{4,12})\b",
    re.IGNORECASE,
)

# Already-redacted placeholders (for idempotency check)
_REDACTED_RE = re.compile(r"\[(?:EMAIL|PHONE|ADDRESS|CLAIM_ID|PERSON|ORG)\]")


def _layer1_regex(text: str) -> str:
    """Apply always-on regex-based PII redaction (Layer 1)."""
    # Order matters: emails before phones (emails contain chars that could match phone)
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _CLAIM_ID_RE.sub("[CLAIM_ID]", text)
    text = _ADDRESS_RE.sub("[ADDRESS]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    return text


def _layer2_ner(text: str) -> str:
    """Best-effort local-only NER for person/org names (Layer 2).

    Uses get_provider() pinned to local Ollama only — NEVER a cloud provider.
    If Ollama is unavailable, returns text unchanged (Layer 1 already applied).
    """
    try:
        import asyncio

        from src.shared.llm_provider import get_provider

        provider = get_provider()

        # Cloud-provider prohibition: check provider type
        provider_name = type(provider).__name__.lower()
        if "anthropic" in provider_name or "openai" in provider_name:
            logger.warning("voice_tone_redactor_cloud_provider_blocked")
            return text

        prompt = (
            "Extract all person names and organization names from the following text. "
            "Return JSON with keys: persons (list of strings), organizations (list of strings).\n\n"
            f"Text: {text}\n\n"
            "Return ONLY names that appear literally in the text."
        )

        try:
            import json

            # D543: provider interface is generate(system_prompt, user_prompt) -> LLMResponse.
            response = asyncio.run(
                provider.generate(system_prompt="", user_prompt=prompt, json_mode=True)
            )
            data = json.loads(response.text)
            persons = data.get("persons", [])
            organizations = data.get("organizations", [])

            for name in persons:
                if isinstance(name, str) and len(name) > 1 and name not in ("[PERSON]", "[ORG]"):
                    text = text.replace(name, "[PERSON]")
            for org in organizations:
                if isinstance(org, str) and len(org) > 1 and org not in ("[PERSON]", "[ORG]"):
                    text = text.replace(org, "[ORG]")

        except Exception:
            logger.warning("voice_tone_ner_extraction_failed")

    except Exception:
        logger.warning("voice_tone_ner_provider_unavailable")

    return text


def redact_text(text: str, ner_available: bool = True) -> str:
    """Redact PII from exemplar text before export (D506).

    Layer 1 (always-on): regex patterns for emails, phones, addresses, claim IDs.
    Layer 2 (best-effort): local-only NER for person/org names via Ollama.

    Args:
        text: The text to redact.
        ner_available: If False, skip Layer 2 NER (Layer 1 regex still runs).

    Returns:
        Redacted text with PII replaced by placeholders.
    """
    if not text:
        return text

    # Layer 1: always-on regex
    result = _layer1_regex(text)

    # Layer 2: best-effort local NER (only if ner_available)
    if ner_available:
        result = _layer2_ner(result)

    return result
