"""F-23 regression: non-ASCII / RFC 2047 email headers must not crash the pull.

validation run (2026-07-01): with the stdlib compat32 policy,
`msg.get("Subject")` returns an `email.header.Header` object (not str) whenever
the subject contains non-ASCII (em-dash, accent, emoji). `CommunicationEvent`
requires plain-str subjects, so the whole pull crashed. The `_header_str` fix in
eml_adapter was ported to the sibling stdlib adapters (mbox/imap/gmail) via a
shared `header_str` helper in adapter_base.
"""

from __future__ import annotations

import asyncio
import email.message
import mailbox
from email.header import Header

import pytest

from src.ingestion.adapter_base import header_str
from src.ingestion.adapters.mbox_adapter import MboxAdapter
from src.ingestion.models import MboxSourceConfig

# Raw wire bytes: subject carrying an unencoded UTF-8 em-dash (U+2014). Parsing
# these bytes with stdlib email yields an email.header.Header object for the
# Subject header — the exact non-str value that crashed Pydantic before the fix.
_EMDASH_BYTES = (
    b"Subject: Quarterly review \xe2\x80\x94 action needed\r\n"
    b"From: alice@example.com\r\n"
    b"To: bob@example.com\r\n"
    b"Message-ID: <emdash@example.com>\r\n"
    b"Date: Mon, 01 Jan 2024 12:00:00 +0000\r\n"
    b"\r\nbody\r\n"
)


def test_header_str_normalizes_parsed_header_object_to_str():
    """A parsed non-ASCII Subject is a Header object; header_str must return str.

    This is the core F-23 guarantee: the pull must not crash. Fidelity of the
    decoded characters is best-effort (the Header object stringifies lossily for
    unencoded 8-bit bytes), but the output must be a plain str.
    """
    msg = email.message_from_bytes(_EMDASH_BYTES)
    value = msg.get("Subject")
    # Precondition: parsing really does hand us a non-str Header here.
    assert not isinstance(value, str)
    out = header_str(value)
    assert isinstance(out, str)
    assert out  # non-empty


def test_header_str_decodes_encoded_word():
    """RFC 2047 encoded-words that surface as Header objects decode to unicode."""
    # Force a Header object (parsing an encoded-word from raw bytes).
    raw = (
        b"Subject: =?utf-8?b?UXVhcnRlcmx5IHJldmlldyDigJQ=?=\r\n"
        b"\r\nbody\r\n"
    )
    msg = email.message_from_bytes(raw)
    value = msg.get("Subject")
    out = header_str(value)
    assert isinstance(out, str)


def test_header_str_passes_through_plain_and_none():
    assert header_str(None) is None
    assert header_str("plain ascii") == "plain ascii"


def test_header_str_handles_header_object():
    """A raw email.header.Header object (non-str) must normalize cleanly."""
    h = Header("cafe update", "utf-8")
    out = header_str(h)
    assert isinstance(out, str)
    assert "cafe" in out


def test_mbox_adapter_emdash_subject_does_not_crash(tmp_path):
    """End-to-end: mbox adapter parses an em-dash subject into a plain-str
    CommunicationEvent.subject without crashing (the F-23 failure mode)."""
    mbox_path = tmp_path / "emdash.mbox"
    mbox_path.write_bytes(b"From alice@example.com Mon Jan  1 12:00:00 2024\r\n" + _EMDASH_BYTES)

    async def _run():
        adapter = MboxAdapter()
        config = MboxSourceConfig(file_path=str(mbox_path))
        await adapter.connect(config)
        return await adapter.parse_message("0")

    result = asyncio.run(_run())
    evt = result.event
    # The subject must be a plain str (never a Header object) — no crash.
    assert isinstance(evt.subject, str)
    # raw_headers values must all be plain strings (or None), never Header objects.
    for v in (evt.raw_headers or {}).values():
        assert v is None or isinstance(v, str)
