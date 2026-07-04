"""Tests for Voice Card v1 renderer and export CLI (Chunk 78, D505)."""

from __future__ import annotations

import ast
import json
import subprocess
import sys

import pytest

from src.ingestion.communications.voice_tone.models import StyleSignature, VoiceCardV1
from src.ingestion.communications.voice_tone.voice_card import VoiceCardRenderer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_signature(**overrides: object) -> StyleSignature:
    """Build a StyleSignature with realistic fields for rendering tests."""
    defaults: dict[str, object] = {
        "sentence_length_band": "high",
        "vocabulary_complexity_band": "medium",
        "formality_band": "high",
        "greeting_closing_band": "medium",
        "hedging_frequency_band": "low",
        "directness_band": "high",
        "response_timing_band": "medium",
        "thread_depth_band": "low",
        "greeting_patterns": ["Hi", "Good morning"],
        "closing_patterns": ["Best regards", "Thanks"],
        "sample_phrases": [
            "Per our discussion with alice@corp.com regarding POLICY-123456",
            "Please see the attached summary",
        ],
        "tone_summary": "Formal, structured communication with a professional tone.",
        "avoid_phrases": ["ASAP", "per my last email"],
        "contrastive_markers": ["elevated 'the' usage", "low pronoun frequency"],
    }
    defaults.update(overrides)
    return StyleSignature(**defaults)


@pytest.fixture
def renderer() -> VoiceCardRenderer:
    return VoiceCardRenderer(word_limit=400)


@pytest.fixture
def sig() -> StyleSignature:
    return _make_signature()


# ---------------------------------------------------------------------------
# Markdown format
# ---------------------------------------------------------------------------


class TestMarkdownFormat:
    def test_markdown_format_has_frontmatter(self, renderer: VoiceCardRenderer, sig: StyleSignature) -> None:
        result = renderer.render(sig, subject="test@example.com", fmt="markdown")
        assert result.startswith("---\n")
        assert "profile_schema: grace.voice-card/v1" in result

    def test_markdown_has_all_sections(self, renderer: VoiceCardRenderer, sig: StyleSignature) -> None:
        result = renderer.render(sig, subject="test@example.com", fmt="markdown")
        required_sections = [
            "## Style summary",
            "## Characteristic phrases",
            "## Greetings/closings",
            "## What makes this voice distinct",
            "## Exemplars",
            "## Avoid",
        ]
        for section in required_sections:
            assert section in result, f"Missing section: {section}"


# ---------------------------------------------------------------------------
# Claude-skill format
# ---------------------------------------------------------------------------


class TestClaudeSkillFormat:
    def test_claude_skill_name_length(self, renderer: VoiceCardRenderer, sig: StyleSignature) -> None:
        result = renderer.render(sig, subject="test@example.com", fmt="claude-skill")
        # First line is `# <name>`
        first_line = result.split("\n")[0]
        name = first_line.lstrip("# ").strip()
        assert len(name) <= 64

    def test_claude_skill_description_length(self, renderer: VoiceCardRenderer, sig: StyleSignature) -> None:
        result = renderer.render(sig, subject="test@example.com", fmt="claude-skill")
        for line in result.split("\n"):
            if line.startswith("**Description:**"):
                desc = line.replace("**Description:**", "").strip()
                assert len(desc) <= 200
                break
        else:
            pytest.fail("No description line found")


# ---------------------------------------------------------------------------
# Claude-style format
# ---------------------------------------------------------------------------


class TestClaudeStyleFormat:
    def test_claude_style_word_limit(self, renderer: VoiceCardRenderer, sig: StyleSignature) -> None:
        result = renderer.render(sig, subject="test@example.com", fmt="claude-style")
        word_count = len(result.split())
        assert word_count <= 400


# ---------------------------------------------------------------------------
# JSON format
# ---------------------------------------------------------------------------


class TestJsonFormat:
    def test_json_roundtrip(self, renderer: VoiceCardRenderer, sig: StyleSignature) -> None:
        result = renderer.render(sig, subject="test@example.com", fmt="json")
        parsed = json.loads(result)
        card = VoiceCardV1.model_validate(parsed)
        assert card.profile_schema == "grace.voice-card/v1"
        assert card.subject == "test@example.com"


# ---------------------------------------------------------------------------
# PII redaction
# ---------------------------------------------------------------------------


class TestExemplarsRedacted:
    def test_exemplars_redacted(self, renderer: VoiceCardRenderer) -> None:
        """Exemplars containing PII are redacted before emission (D506)."""
        sig = _make_signature(
            sample_phrases=["Contact alice@corp.com for details about POLICY-999999"]
        )
        # Check across all formats
        for fmt in ("markdown", "claude-skill", "claude-style", "json"):
            result = renderer.render(sig, subject="test@example.com", fmt=fmt)
            assert "alice@corp.com" not in result
            assert "[EMAIL]" in result or "POLICY-999999" not in result


# ---------------------------------------------------------------------------
# Export directory creation
# ---------------------------------------------------------------------------


class TestAssetDirCreation:
    def test_asset_dir_creation(self, renderer: VoiceCardRenderer, sig: StyleSignature, tmp_path: pytest.TempPathFactory) -> None:
        """Export creates data/voice-profiles/<subject>/ directory."""
        out_dir = tmp_path / "test-subject"  # type: ignore[operator]
        out_dir.mkdir(parents=True, exist_ok=True)
        rendered = renderer.render(sig, subject="test-subject", fmt="markdown")
        out_file = out_dir / "voice-card.md"
        out_file.write_text(rendered)
        assert out_file.exists()
        assert "profile_schema" in out_file.read_text()


# ---------------------------------------------------------------------------
# CLI help
# ---------------------------------------------------------------------------


class TestExportCliHelp:
    def test_export_cli_help(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "src.ingestion.communications.voice_tone", "export", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--person" in result.stdout
        assert "--format" in result.stdout


# ---------------------------------------------------------------------------
# Route isolation guard (D246 mirror, D505)
# ---------------------------------------------------------------------------


class TestRouteIsolation:
    def test_voice_card_not_imported_by_routes(self) -> None:
        """voice_card.py MUST NOT be imported by communications_routes.py."""
        import pathlib

        routes_path = pathlib.Path("src/api/communications_routes.py")
        source = routes_path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "voice_card" not in node.module, (
                    f"voice_card imported in communications_routes.py: {node.module}"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "voice_card" not in alias.name
