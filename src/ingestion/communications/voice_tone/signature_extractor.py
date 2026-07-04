"""Signature extractor for Voice & Tone Profiling (Chunk 58, CP5, Lock-R1).

Detects email signature blocks, extracts structured fields (title, organization),
maps to D422 categories via ``title_to_category_map`` config, with LLM fallback.

Note: ``talon`` (spec §6 CP5) fails to install on Python 3.14 due to ``cchardet``
build dependency. Regex-based signature block detection is used as the primary
detector per R1 mitigation (LLM fallback for unconventional signatures).
The ``talon``-based path activates automatically if the library becomes installable.
"""

from __future__ import annotations

import re
from typing import Literal

import structlog

from src.ingestion.communications.voice_tone.models import Band, D422_CATEGORIES, VoiceToneConfig

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Signature block detection patterns
# ---------------------------------------------------------------------------

# Common signature delimiters
_SIG_DELIMITERS = [
    re.compile(r"^--\s*$", re.MULTILINE),  # RFC "-- " delimiter
    re.compile(r"^_{3,}$", re.MULTILINE),  # underscores
    re.compile(r"^-{3,}$", re.MULTILINE),  # dashes
    re.compile(r"^Sent from my", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^Get Outlook for", re.MULTILINE | re.IGNORECASE),
]

# Title patterns (case-insensitive)
_TITLE_RE = re.compile(
    r"(?:^|\n)\s*((?:chief|senior|junior|associate|managing|executive|general|deputy|assistant|vice)\s+)?"
    r"(president|director|manager|officer|analyst|engineer|consultant|counsel|attorney|"
    r"partner|associate|intern|supervisor|coordinator|specialist|administrator|"
    r"vp|ceo|cfo|coo|cto|cio|cmo)\b",
    re.IGNORECASE,
)

# Phone pattern
_PHONE_RE = re.compile(r"[\+]?[\d\s\-\(\)]{7,15}")

# Email in signature
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _try_talon_detect(body: str) -> str | None:
    """Try talon for signature block detection. Returns None if unavailable."""
    try:
        from talon import signature  # type: ignore[import-untyped]

        text, sig = signature.extract(body, sender="")
        return sig
    except ImportError:
        return None
    except Exception:
        return None


def _regex_detect_signature(body: str) -> str | None:
    """Regex-based signature block detection (fallback when talon unavailable)."""
    lines = body.split("\n")
    if len(lines) < 2:
        return None

    # Try each delimiter pattern
    for pattern in _SIG_DELIMITERS:
        match = pattern.search(body)
        if match:
            sig_text = body[match.start():]
            # Signature blocks are typically < 15 lines
            sig_lines = sig_text.split("\n")
            if len(sig_lines) <= 15:
                return sig_text

    # Heuristic: look for signature-like content in last 8 lines
    tail = lines[-8:]
    tail_text = "\n".join(tail)

    # Check for title or phone in tail
    if _TITLE_RE.search(tail_text) or _PHONE_RE.search(tail_text):
        return tail_text

    return None


class SignatureExtractor:
    """Email signature extractor with title/org extraction and LLM fallback.

    Uses talon for signature-block detection when available, falling back to
    regex patterns. Title-to-category mapping via ``title_to_category_map``
    from config (Lock-R1: organization_domains in voice_tone_config.yaml).
    """

    def __init__(self, config: VoiceToneConfig) -> None:
        self.config = config
        self._org_domains_warned = False

    def detect_signature(self, body: str) -> str | None:
        """Detect and extract signature block from email body.

        Tries talon first, falls back to regex.
        """
        sig = _try_talon_detect(body)
        if sig is not None:
            return sig
        return _regex_detect_signature(body)

    def extract_title(self, signature_text: str) -> str | None:
        """Extract job title from signature text using regex."""
        match = _TITLE_RE.search(signature_text)
        if match:
            title = match.group(0).strip()
            return title
        return None

    def extract_organization(
        self, signature_text: str, sender_email: str
    ) -> str | None:
        """Extract organization from signature or email domain.

        Lock-R1: organization detection via domain-match against
        ``organization_domains`` in config.
        """
        # Check email domain against organization_domains
        domain = sender_email.split("@")[-1].lower() if "@" in sender_email else None
        if domain and domain in self.config.organization_domains:
            return domain

        # Last-line heuristic: look for org name in signature
        lines = [l.strip() for l in signature_text.split("\n") if l.strip()]
        # Look for a line that's not a phone/email/title — likely org name
        for line in lines:
            if not _PHONE_RE.fullmatch(line) and not _EMAIL_RE.fullmatch(line):
                if not _TITLE_RE.search(line) and len(line) > 2 and len(line) < 80:
                    # Likely org name
                    return line

        return None

    def classify_internal_external(
        self, sender_email: str
    ) -> Literal["internal", "external"]:
        """Classify sender as internal or external based on organization_domains.

        Lock-R1: ``email_domain in organization_domains → internal; else → external``.
        If ``organization_domains`` is empty, degrades to "external for all" with
        structlog warning.
        """
        if not self.config.organization_domains:
            if not self._org_domains_warned:
                logger.warning("voice_tone_no_organization_domains_configured")
                self._org_domains_warned = True
            return "external"

        domain = sender_email.split("@")[-1].lower() if "@" in sender_email else ""
        if domain in self.config.organization_domains:
            return "internal"
        return "external"

    def title_to_category(self, title: str) -> tuple[str | None, Band]:
        """Map a signature title to a D422 category via title_to_category_map.

        Returns (category, confidence_band). None if no match.
        """
        title_lower = title.lower().strip()
        for pattern, category in self.config.title_to_category_map.items():
            if pattern.lower() in title_lower:
                if category in D422_CATEGORIES:
                    return category, "medium"
        return None, "low"

    async def extract_with_llm_fallback(
        self, signature_text: str
    ) -> dict:
        """LLM fallback for unconventional signatures (R1 mitigation).

        Uses ``LLMProvider.generate(json_mode=True)`` when regex fails.
        """
        from src.shared.llm_provider import get_provider

        provider = get_provider()
        prompt = (
            "Extract the following from this email signature block:\n"
            "- title: the person's job title (null if not found)\n"
            "- organization: the company/organization name (null if not found)\n"
            "- phone: phone number (null if not found)\n\n"
            f"Signature:\n{signature_text}\n\n"
            "Return JSON with keys: title, organization, phone."
        )

        try:
            # D543: provider interface is generate(system_prompt, user_prompt) -> LLMResponse.
            response = await provider.generate(system_prompt="", user_prompt=prompt, json_mode=True)
            import json
            return json.loads(response.text)
        except Exception:
            logger.warning("voice_tone_signature_llm_fallback_failed")
            return {"title": None, "organization": None, "phone": None}
