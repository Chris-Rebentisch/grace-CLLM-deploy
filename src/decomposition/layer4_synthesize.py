"""Layer 4 — hypothesis synthesis (Chunk 40, D314 + D317).

Calls ``LLMProvider.generate()`` (Instructor structured-output style)
with a few-shot prompt assembled from Layers 1–3 artifacts and parses
the response into a ``Layer4HypothesisSet`` (which enforces
exactly-one-NullHypothesis via ``@model_validator``). When Layer 3
flags low stability the literal context block is injected into the
user prompt; otherwise it is omitted.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import structlog

from src.decomposition.config import DecompositionConfig
from src.decomposition.models import (
    Layer1Summary,
    Layer2Decision,
    Layer3Decision,
    Layer4HypothesisSet,
    SynthesisMetadata,
)


log = structlog.get_logger()

# F-032d / ISS-0021: the tier-b prompt's segment field vocabulary must
# match the ``ProposedSegment`` Pydantic model exactly (``name``, not the
# legacy ``segment_name``; no ``segment_id``). The prompt now spells this
# out and the models additionally accept the legacy aliases
# (``src/decomposition/models.py``) so either shape validates instead of
# pausing every run at ``paused_pre_layer4``.
_PROMPT_FILE = Path(__file__).parent / "prompts" / "layer4_synthesis.txt"

_LOW_STABILITY_CONTEXT = (
    "NOTE — Layer 3 flagged low stability "
    "(mean pairwise ARI below the configured threshold). Treat "
    "community structure as tentative; weight the null hypothesis "
    "and ambiguous segmented hypotheses accordingly."
)


class LLMLike(Protocol):
    """Subset of ``LLMProvider`` used by the synthesizer."""

    async def generate(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        ...


def _read_prompt() -> tuple[str, str]:
    raw = _PROMPT_FILE.read_text(encoding="utf-8")
    parts = raw.split("==USER==", 1)
    system = parts[0].replace("==SYSTEM==", "").strip()
    user = parts[1].strip() if len(parts) == 2 else ""
    return system, user


def _normalize_response(resp: Any) -> str:
    if hasattr(resp, "text"):
        return resp.text
    if isinstance(resp, str):
        return resp
    if isinstance(resp, (dict, list)):
        return json.dumps(resp)
    if hasattr(resp, "model_dump"):
        return json.dumps(resp.model_dump(mode="json"))
    return json.dumps(resp)


def _build_user_prompt(
    template: str,
    layer1: Layer1Summary,
    layer2: Layer2Decision,
    layer3: Layer3Decision,
) -> str:
    low_stability_context = (
        _LOW_STABILITY_CONTEXT if layer3.low_stability_flag else ""
    )
    return (
        template.replace(
            "{layer1_summary}", json.dumps(layer1.model_dump(mode="json"))
        )
        .replace(
            "{layer2_decision}", json.dumps(layer2.model_dump(mode="json"))
        )
        .replace(
            "{layer3_decision}",
            json.dumps(
                # Drop community_assignments to keep the prompt small;
                # leiden_runs + ARI signal are sufficient for synthesis.
                {
                    "document_count": layer3.document_count,
                    "edge_count": layer3.edge_count,
                    "selected_seed": layer3.selected_seed,
                    "selected_modularity": layer3.selected_modularity,
                    "mean_pairwise_ari": layer3.mean_pairwise_ari,
                    "low_stability_flag": layer3.low_stability_flag,
                }
            ),
        )
        .replace("{low_stability_context}", low_stability_context)
    )


def _strip_code_fences(text: str) -> str:
    payload = text.strip()
    if payload.startswith("```"):
        payload = payload.strip("`")
        if payload.lower().startswith("json"):
            payload = payload[4:]
        payload = payload.strip()
    return payload


def _coerce_metadata(
    raw: dict, layer3: Layer3Decision, model: str
) -> dict:
    """Inject ``synthesis_metadata`` defaults if the LLM omitted them."""
    meta = raw.get("synthesis_metadata")
    if meta is None:
        raw["synthesis_metadata"] = SynthesisMetadata(
            model=model,
            low_stability_flag=layer3.low_stability_flag,
            layer3_mean_pairwise_ari=layer3.mean_pairwise_ari,
            generated_at=datetime.now(timezone.utc),
        ).model_dump(mode="json")
    return raw


async def synthesize_hypotheses(
    layer1: Layer1Summary,
    layer2: Layer2Decision,
    layer3: Layer3Decision,
    llm_provider: LLMLike,
    config: DecompositionConfig,
) -> Layer4HypothesisSet:
    """Run Layer 4 synthesis and return a validated ``Layer4HypothesisSet``.

    Raises ``ValueError`` when the LLM response cannot be parsed or
    fails ``Layer4HypothesisSet.@model_validator`` (0 or 2 null
    hypotheses, schema mismatch, etc.). The orchestrator catches this
    and sets ``status='paused_pre_layer4'``.
    """
    system_prompt, user_template = _read_prompt()
    user_prompt = _build_user_prompt(user_template, layer1, layer2, layer3)

    # Phase-6 fix: prefer grammar-constrained ``generate_structured`` (D444)
    # when the provider implements it so Haiku / OpenAI-strict don't
    # rename the top-level field. Fall back to ``generate`` for Ollama
    # legacy paths and for unit-test mocks.
    resp = None
    if hasattr(llm_provider, "generate_structured"):
        try:
            resp = await llm_provider.generate_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=Layer4HypothesisSet,
            )
            # ``LLMResponse.parsed`` carries the validated instance.
            parsed = getattr(resp, "parsed", None)
            if isinstance(parsed, Layer4HypothesisSet):
                return parsed
        except (TypeError, NotImplementedError):
            resp = None
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "layer4.generate_structured_failed_falling_back",
                error=str(exc),
            )
            resp = None

    if resp is None:
        try:
            resp = await llm_provider.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=Layer4HypothesisSet,
            )
        except TypeError:
            # Mocks may not accept ``response_model``; retry without it.
            try:
                resp = await llm_provider.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
            except TypeError:
                resp = await llm_provider.generate(user_prompt)

    if isinstance(resp, Layer4HypothesisSet):
        return resp

    text = _normalize_response(resp)
    payload = _strip_code_fences(text)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Layer 4 LLM did not return JSON: {exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError("Layer 4 LLM JSON must be an object")

    data = _coerce_metadata(
        data, layer3, model=getattr(config.layer3.ner, "model", "unknown")
    )
    data = _normalize_hypothesis_keys(data)
    try:
        return Layer4HypothesisSet.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"Layer 4 LLM output failed schema validation: {exc}"
        ) from exc


def _normalize_hypothesis_keys(data: dict) -> dict:
    """Map known LLM field-name drift onto the schema's field names (F-59).

    F-59 (validation run, 2026-07-02) capture-the-why: with the Anthropic
    provider, Claude emits ``segment_name`` where ``NullHypothesis`` /
    ``SegmentedHypothesis`` require ``name`` (12 validation errors →
    ``paused_pre_layer4``). The D444 grammar-constrained tier does not strictly
    hold on the Anthropic path (Ollama's XGrammar would force the keys), so this
    tolerant normalization renames the known alias before validation. Provider-
    agnostic: a no-op when the model already returned ``name``.
    """
    hyps = data.get("hypotheses")
    if isinstance(hyps, list):
        for h in hyps:
            if isinstance(h, dict) and "name" not in h and "segment_name" in h:
                h["name"] = h.pop("segment_name")
    return data
