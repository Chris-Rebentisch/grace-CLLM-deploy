"""Tier 1 noise filter tests (Chunk 56 CP2 — 14 tests)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.ingestion.communications.triage.config import Tier1Config
from src.ingestion.communications.triage.tier1_noise import (
    _strip_html,
    check_auto_reply,
    check_bounce,
    check_calendar_invite,
    check_duplicate_message_id,
    check_empty_body,
    check_newsletter,
    check_system_notification,
    run_tier1,
)
from src.ingestion.models import AttachmentRef, CommunicationEvent


def _make_event(**overrides) -> CommunicationEvent:
    defaults = dict(
        source_id=uuid4(),
        message_id=f"<{uuid4()}@example.com>",
        sender_email="alice@example.com",
        body_plain="Hello, this is a test email with enough content.",
        source_type="mbox",
    )
    defaults.update(overrides)
    return CommunicationEvent(**defaults)


# --- Individual rule fires ---

def test_duplicate_message_id_fires():
    ev = _make_event(message_id="<dup@example.com>")
    seen = {"<dup@example.com>"}
    assert check_duplicate_message_id(ev, seen) == "filtered_t1_duplicate_message_id"


def test_auto_reply_fires():
    ev = _make_event(raw_headers={"Auto-Submitted": "auto-replied"})
    assert check_auto_reply(ev) == "filtered_t1_auto_reply"


def test_newsletter_fires():
    ev = _make_event(raw_headers={"List-Unsubscribe": "<mailto:unsub@example.com>"})
    assert check_newsletter(ev) == "filtered_t1_newsletter"


def test_calendar_invite_fires():
    att = AttachmentRef(filename="invite.ics", mime_type="text/calendar", size_bytes=1024)
    ev = _make_event(attachments=[att])
    assert check_calendar_invite(ev) == "filtered_t1_calendar_invite"


def test_bounce_fires():
    ev = _make_event(raw_headers={"Content-Type": "multipart/report; boundary=abc"})
    assert check_bounce(ev) == "filtered_t1_bounce"


def test_system_notification_fires():
    ev = _make_event(sender_email="noreply@company.com")
    assert check_system_notification(ev, ["noreply@"]) == "filtered_t1_system_notification"


def test_empty_body_fires():
    ev = _make_event(body_plain="Hi", body_html=None)
    assert check_empty_body(ev, min_chars=20) == "filtered_t1_empty_body"


# --- Individual rule skips (disabled) ---

def test_auto_reply_no_header():
    ev = _make_event(raw_headers={"Auto-Submitted": "no"})
    assert check_auto_reply(ev) is None


def test_newsletter_no_header():
    ev = _make_event(raw_headers={})
    assert check_newsletter(ev) is None


# --- strip_html ---

def test_strip_html():
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"


# --- Precedence and run_tier1 ---

def test_run_tier1_precedence():
    """auto_reply fires before newsletter when both present."""
    ev = _make_event(
        raw_headers={"Auto-Submitted": "auto-replied", "List-Unsubscribe": "<url>"},
    )
    cfg = Tier1Config(rule_order=["auto_reply", "newsletter"])
    assert run_tier1(ev, cfg, seen_ids=set()) == "filtered_t1_auto_reply"


def test_run_tier1_full_pass():
    """No rule fires → returns None."""
    ev = _make_event(body_plain="This email has plenty of content to pass all filters.")
    cfg = Tier1Config(
        rule_order=["duplicate_message_id", "auto_reply", "newsletter",
                     "calendar_invite", "bounce", "system_notification", "empty_body"],
    )
    assert run_tier1(ev, cfg, seen_ids=set()) is None


def test_run_tier1_disabled_rule_skipped():
    """Disabled rule is skipped even when it would fire."""
    ev = _make_event(raw_headers={"Auto-Submitted": "auto-replied"})
    from src.ingestion.communications.triage.config import Tier1RuleConfig
    cfg = Tier1Config(
        rule_order=["auto_reply"],
        auto_reply=Tier1RuleConfig(enabled=False),
    )
    assert run_tier1(ev, cfg, seen_ids=set()) is None


def test_run_tier1_duplicate_seen_ids():
    """Duplicate message_id caught via seen_ids set."""
    ev = _make_event(message_id="<dup@test.com>")
    cfg = Tier1Config(rule_order=["duplicate_message_id"])
    seen = {"<dup@test.com>"}
    assert run_tier1(ev, cfg, seen_ids=seen) == "filtered_t1_duplicate_message_id"
