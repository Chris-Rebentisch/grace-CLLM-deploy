"""Corroboration scorer — CLI-only (D515/D516/D517, D246 mirror).

Computes per-entity trust scores from email-derived graph entities using:
- v1: noisy-OR closed-form scorer (D515, default)
- v2: iterative TruthFinder mode (D516, opt-in via --v2 or config)

Promotion gate (D517): entities meeting threshold, sender count, and
agreement conditions are promoted to 'first_class'; others stay 'provisional'.

D356 capture-the-why: D515/D516/D517 — noisy-OR corroboration scorer,
iterative TruthFinder, promotion gate. D246 mirror — MUST NOT import
fastapi or apscheduler.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger()

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "corroboration_config.yaml"


@dataclass
class CorroborationConfig:
    """Parsed corroboration configuration."""

    theta_promote: float = 0.85
    k_senders: int = 2
    lambda_contradict: float = 0.7
    max_iters: int = 10
    delta: float = 0.001
    iterative: bool = False
    priors: dict[str, float] = field(default_factory=lambda: {
        "canonical": 0.85,
        "internal_domain": 0.75,
        "external_known_service": 0.70,
        "unknown": 0.50,
    })
    quality_weights: dict[str, float] = field(default_factory=lambda: {
        "reply_affirm": 1.0,
        "clear_assertion": 0.8,
        "incidental": 0.5,
    })
    affirm_cues: list[str] = field(default_factory=list)
    contradict_cues: list[str] = field(default_factory=list)
    # D542: graph vertex labels used to resolve an email sender to a canonical
    # person/org (so a resolved sender scores as 'canonical' reliability and
    # distinct resolved senders corroborate). Mirrors triage D540 — set to your
    # ontology's sender types when the defaults don't cover them. C1 defect #3:
    # the default is a superset of the shipped pair (adds Legal_Entity for
    # legal-ontology deployments); an absent label resolves as no-match, not error.
    sender_entity_types: list[str] = field(
        default_factory=lambda: ["Person", "Organization", "Legal_Entity"]
    )


def load_config(path: Path | None = None) -> CorroborationConfig:
    """Load corroboration config from YAML."""
    config_path = path or _CONFIG_PATH
    if not config_path.exists():
        logger.warning("corroboration.config_missing", path=str(config_path))
        return CorroborationConfig()
    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}
    defaults = CorroborationConfig()
    return CorroborationConfig(
        theta_promote=raw.get("theta_promote", 0.85),
        k_senders=raw.get("k_senders", 2),
        lambda_contradict=raw.get("lambda_contradict", 0.7),
        max_iters=raw.get("max_iters", 10),
        delta=raw.get("delta", 0.001),
        iterative=raw.get("iterative", False),
        priors=raw.get("priors", defaults.priors),
        quality_weights=raw.get("quality_weights", defaults.quality_weights),
        affirm_cues=raw.get("affirm_cues", []),
        contradict_cues=raw.get("contradict_cues", []),
        sender_entity_types=raw.get("sender_entity_types", defaults.sender_entity_types),
    )


# ---------------------------------------------------------------------------
# Stance classifier (rule-based + LLM fallback)
# ---------------------------------------------------------------------------


def _cue_in_text(cue: str, text_lower: str) -> bool:
    """Whole-word/phrase cue match.

    D549 — capture-the-why: cue matching was a naive substring test
    (`cue.lower() in text_lower`), so a short cue like ``"no"`` matched INSIDE
    unrelated words — ``"no"`` in ``"Notice"``, ``"Alice"``, ``"cannot"`` — and
    flagged plainly-affirming emails as ``contradict``. That collapses corroboration
    scoring: ``s_plus`` drops to 0 and nothing ever promotes (a real email corpus
    where most messages contain some word spelled with the cue inside it is
    permanently un-corroboratable). Word-boundary anchoring (``\\bcue\\b``) matches
    the cue as a standalone word or phrase only; multi-word cues ("not true",
    "I disagree") are anchored at the phrase edges. Surfaced live by the
    bounded-heat apply-gate (Finding #17).
    """
    return re.search(r"\b" + re.escape(cue.lower()) + r"\b", text_lower) is not None


def classify_stance(
    text: str,
    config: CorroborationConfig,
    *,
    use_llm_fallback: bool = False,
) -> str:
    """Classify mention stance: 'affirm', 'contradict', or 'incidental'.

    Rule-based cue-list match first. Falls back to LLM via get_provider()
    for ambiguous cases when use_llm_fallback=True (D232 airgap guard applies).
    """
    text_lower = text.lower().strip()

    # Rule-based: check contradict cues first (more specific)
    for cue in config.contradict_cues:
        if _cue_in_text(cue, text_lower):
            return "contradict"

    # Rule-based: check affirm cues
    for cue in config.affirm_cues:
        if _cue_in_text(cue, text_lower):
            return "affirm"

    # LLM fallback for ambiguous cases
    if use_llm_fallback:
        try:
            from src.shared.llm_provider import get_provider

            import asyncio

            provider = get_provider()
            # D543: provider.generate is async with signature (system_prompt, user_prompt);
            # classify_stance is sync, so drive it with asyncio.run. (This fallback is
            # only reached when use_llm_fallback=True — the sanctioned CLI never enables it.)
            resp = asyncio.run(provider.generate(
                system_prompt="",
                user_prompt=(
                    "Classify the following email text as 'affirm', 'contradict', or "
                    "'incidental' regarding factual claims. Reply with exactly one word."
                    f"\n\nText: {text[:500]}"
                ),
            ))
            label = resp.text.strip().lower()
            if label in {"affirm", "contradict", "incidental"}:
                return label
        except Exception:
            logger.debug("corroboration.llm_fallback_failed")

    return "incidental"


# ---------------------------------------------------------------------------
# Source identity model
# ---------------------------------------------------------------------------


@dataclass
class SourceMention:
    """A mention of an entity by a resolved Person source."""

    person_id: str  # grace_id of the resolved Person
    person_category: str  # priors category key
    stance: str  # affirm | contradict | incidental
    quality_key: str  # quality_weights key
    text_snippet: str = ""  # for dedup — the visible text (post-quote-strip)
    message_id: str = ""  # RFC 5322 message_id for dedup


@dataclass
class EntityCorroboration:
    """Corroboration data for a single entity."""

    entity_grace_id: str
    entity_type: str
    mentions: list[SourceMention] = field(default_factory=list)

    def distinct_person_ids(self) -> set[str]:
        """Distinct resolved Person originators (echo-dedup — R1)."""
        return {m.person_id for m in self.mentions}

    def agreeing_mentions(self) -> list[SourceMention]:
        """Mentions with affirm stance (W+)."""
        return [m for m in self.mentions if m.stance == "affirm"]

    def contradicting_mentions(self) -> list[SourceMention]:
        """Mentions with contradict stance (W-)."""
        return [m for m in self.mentions if m.stance == "contradict"]


# ---------------------------------------------------------------------------
# v1 Noisy-OR scorer (D515)
# ---------------------------------------------------------------------------


@dataclass
class CorroborationScore:
    """Result of corroboration scoring."""

    entity_grace_id: str
    s_plus: float  # agreement aggregate
    s_minus: float  # contradiction aggregate
    score: float  # c(e) = S_plus * (1 - lambda * S_minus)
    corroborating_sender_count: int
    status: str  # 'first_class' or 'provisional'


def _noisy_or_aggregate(
    mentions: list[SourceMention],
    config: CorroborationConfig,
    *,
    source_reliability: dict[str, float] | None = None,
) -> float:
    """Compute 1 - Π(1 - r(w) * q(w)) for a set of mentions.

    source_reliability overrides per-person priors (used in v2 iterative mode).
    """
    product = 1.0
    for m in mentions:
        if source_reliability and m.person_id in source_reliability:
            r = source_reliability[m.person_id]
        else:
            r = config.priors.get(m.person_category, config.priors.get("unknown", 0.5))
        q = config.quality_weights.get(m.quality_key, config.quality_weights.get("incidental", 0.5))
        product *= (1.0 - r * q)
    return 1.0 - product


def score_entity_v1(
    entity: EntityCorroboration,
    config: CorroborationConfig,
    *,
    source_reliability: dict[str, float] | None = None,
) -> CorroborationScore:
    """Compute v1 closed-form noisy-OR score for an entity."""
    agreeing = entity.agreeing_mentions()
    contradicting = entity.contradicting_mentions()

    s_plus = _noisy_or_aggregate(agreeing, config, source_reliability=source_reliability)
    s_minus = _noisy_or_aggregate(contradicting, config, source_reliability=source_reliability)

    # c(e) = S_plus * (1 - λ * S_minus)
    score = s_plus * (1.0 - config.lambda_contradict * s_minus)

    sender_count = len(entity.distinct_person_ids())

    # Promotion gate (D517)
    if (
        score >= config.theta_promote
        and sender_count >= config.k_senders
        and s_minus < s_plus
    ):
        status = "first_class"
    else:
        status = "provisional"

    return CorroborationScore(
        entity_grace_id=entity.entity_grace_id,
        s_plus=s_plus,
        s_minus=s_minus,
        score=score,
        corroborating_sender_count=sender_count,
        status=status,
    )


# ---------------------------------------------------------------------------
# v2 Iterative TruthFinder mode (D516)
# ---------------------------------------------------------------------------


def score_entities_v2(
    entities: list[EntityCorroboration],
    config: CorroborationConfig,
) -> list[CorroborationScore]:
    """Iterative TruthFinder: EM loop over entity claims and source reliability.

    Log-space accumulation for numerical stability. Convergence when
    cosine-change < delta or max_iters reached (D516).
    """
    # Initialize source reliability from priors
    all_person_ids: set[str] = set()
    person_categories: dict[str, str] = {}
    for entity in entities:
        for m in entity.mentions:
            all_person_ids.add(m.person_id)
            person_categories[m.person_id] = m.person_category

    source_reliability: dict[str, float] = {
        pid: config.priors.get(
            person_categories.get(pid, "unknown"),
            config.priors.get("unknown", 0.5),
        )
        for pid in all_person_ids
    }

    prev_scores: dict[str, float] = {}

    for iteration in range(config.max_iters):
        # E-step: score all entities given current source reliability
        current_scores: dict[str, CorroborationScore] = {}
        for entity in entities:
            cs = score_entity_v1(entity, config, source_reliability=source_reliability)
            current_scores[entity.entity_grace_id] = cs

        # M-step: update source reliability based on entity scores (log-space)
        new_reliability: dict[str, float] = {}
        for pid in all_person_ids:
            log_sum = 0.0
            count = 0
            for entity in entities:
                for m in entity.mentions:
                    if m.person_id == pid and m.stance == "affirm":
                        entity_score = current_scores[entity.entity_grace_id].score
                        if entity_score > 0:
                            log_sum += math.log(max(entity_score, 1e-10))
                            count += 1
            if count > 0:
                new_reliability[pid] = min(1.0, math.exp(log_sum / count))
            else:
                new_reliability[pid] = source_reliability[pid]

        # Convergence check: cosine change
        score_vec = [current_scores[e.entity_grace_id].score for e in entities]
        prev_vec = [prev_scores.get(e.entity_grace_id, 0.0) for e in entities]

        if prev_vec and any(v != 0 for v in prev_vec):
            dot = sum(a * b for a, b in zip(score_vec, prev_vec))
            mag_a = math.sqrt(sum(a * a for a in score_vec)) or 1e-10
            mag_b = math.sqrt(sum(b * b for b in prev_vec)) or 1e-10
            cosine_sim = dot / (mag_a * mag_b)
            change = 1.0 - cosine_sim
            if change < config.delta:
                logger.info(
                    "corroboration.v2_converged",
                    iteration=iteration + 1,
                    cosine_change=change,
                )
                break

        prev_scores = {e.entity_grace_id: current_scores[e.entity_grace_id].score for e in entities}
        source_reliability = new_reliability

    # Final scoring with converged reliability
    results: list[CorroborationScore] = []
    for entity in entities:
        cs = score_entity_v1(entity, config, source_reliability=source_reliability)
        results.append(cs)

    return results


# ---------------------------------------------------------------------------
# Promotion writer (graph update via entity_ops)
# ---------------------------------------------------------------------------


async def promote_entity(
    entity_grace_id: str,
    status: str,
    sender_count: int,
    *,
    dry_run: bool = False,
) -> None:
    """Write corroboration_status + corroborating_sender_count to a graph vertex.

    Uses entity_ops.update_entity to SET the two properties via Cypher.
    D517 — promotion gate write. D274 segment-level gate NOT bypassed.
    """
    from src.analytics.metrics import grace_corroboration_promotions_total

    # Emit OTel counter
    try:
        grace_corroboration_promotions_total.add(1, {"status": status})
    except Exception:  # noqa: BLE001
        pass

    if dry_run:
        logger.info(
            "corroboration.promote_dry_run",
            entity_grace_id=entity_grace_id,
            status=status,
            sender_count=sender_count,
        )
        return

    from src.graph.arcade_client import get_arcade_client
    from src.graph.cypher_utils import escape_cypher_string

    client = get_arcade_client()
    escaped_id = escape_cypher_string(entity_grace_id)
    query = (
        f"MATCH (n {{grace_id: '{escaped_id}'}}) "
        f"SET n.corroboration_status = '{escape_cypher_string(status)}', "
        f"n.corroborating_sender_count = {sender_count} "
        f"RETURN n.grace_id"
    )
    await client.execute_cypher(query)
    logger.info(
        "corroboration.promoted",
        entity_grace_id=entity_grace_id,
        status=status,
        sender_count=sender_count,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    """Build the CLI argument parser (D476 contract-testable)."""
    parser = argparse.ArgumentParser(
        prog="corroboration_scorer",
        description="Corroboration scorer — noisy-OR trust scoring for email-derived entities (D515–D517).",
    )
    sub = parser.add_subparsers(dest="command")
    run_parser = sub.add_parser("run", help="Run corroboration scoring")
    run_parser.add_argument("--dry-run", action="store_true", help="Score without writing to graph")
    run_parser.add_argument("--v2", action="store_true", help="Use iterative TruthFinder mode (D516)")
    run_parser.add_argument("--observation-time", type=str, default=None, help="ISO8601 observation time")
    return parser


# ---------------------------------------------------------------------------
# D542 — run flow: graph entities -> provenance -> senders -> score -> promote
# ---------------------------------------------------------------------------
# Capture-the-why: prior to D542 the `run` CLI was a stub that logged start/complete
# without querying the graph, scoring, or promoting (the documented promotion entry
# point was a no-op; only unit tests exercised the math). This wires the shipped
# scorer to real email-derived graph data via the extraction provenance chain
# (entity --produced_by--> Extraction_Event{source_document_id:"email:<msg_id>"} ->
# communication_events.sender_email -> resolved registry person).


async def _resolve_sender(
    arcade_client: Any, sender_email: str, entity_types: list[str],
    *, cache: dict[str, tuple[str, str]], diag: dict[str, int],
    display_name: str | None = None,
) -> tuple[str, str]:
    """Resolve an email sender to a canonical registry person + reliability category.

    Returns ``(person_grace_id, "canonical")`` when the sender resolves to a registry
    vertex, else ``(sender_email, "unknown")`` so distinct unresolved senders still
    count as distinct originators (echo-dedup keys on this id).

    Hardening (C1 round-3 audit): memoized per run (`cache`) — senders repeat heavily
    across a corpus, so without this the resolution issued one Cypher query per
    (entity, message, label). Ambiguous matches (>1 vertex) are logged and counted —
    an over-broad alias collapsing two distinct senders to one id would silently
    defeat the multi-sender promotion gate. Suppressed query errors are counted +
    logged so a graph outage is distinguishable from a legitimate no-match (the
    silent-no-op-in-production class the C1 fixes exist to kill)."""
    if sender_email in cache:
        return cache[sender_email]
    result = (sender_email, "unknown")
    # F2-07: two-needle resolution mirroring the F-31 voice pattern — the
    # email first, then the RFC-5322 display name. On a fresh graph Person
    # vertices rarely carry email aliases (validation run: 4/4 sampled senders
    # unresolved), but their `name` IS the display name the mail headers
    # carry, so the second needle resolves without any registry seeding.
    needles = [sender_email]
    if display_name and display_name.strip():
        needles.append(display_name.strip())
    for needle in needles:
        for label in entity_types:
            query = (
                f"MATCH (n:{label}) "
                "WHERE $needle IN n.aliases OR n.name = $needle "
                "RETURN n.grace_id AS gid LIMIT 2"  # LIMIT 2 to detect ambiguity cheaply
            )
            try:
                resp = await arcade_client.execute_cypher(query, params={"needle": needle})
            except Exception as exc:  # noqa: BLE001 — label may be absent OR a real outage
                diag["errors_suppressed"] = diag.get("errors_suppressed", 0) + 1
                logger.warning("corroboration.sender_resolve_error", label=label,
                               sender=sender_email, error=str(exc))
                continue
            rows = resp.get("result", []) if isinstance(resp, dict) else resp or []
            if rows:
                if len(rows) > 1:
                    diag["ambiguous_senders"] = diag.get("ambiguous_senders", 0) + 1
                    logger.warning("corroboration.ambiguous_sender_resolution",
                                   label=label, sender=sender_email, matches=len(rows))
                    continue  # ambiguous needle — try the next label/needle
                gid = rows[0].get("gid") or rows[0].get("grace_id") or rows[0].get("n.grace_id")
                if gid:
                    result = (str(gid), "canonical")
                    break
        if result[1] == "canonical":
            break
    if result[1] == "unknown":
        diag["unresolved_senders"] = diag.get("unresolved_senders", 0) + 1
    cache[sender_email] = result
    return result


async def gather_communication_corroborations(
    arcade_client: Any, session: Any, config: CorroborationConfig,
    *, diag: dict[str, int] | None = None,
) -> list[EntityCorroboration]:
    """Build EntityCorroboration objects from email-derived graph entities.

    `diag` (optional) accumulates skip/error counters so a silently-degraded run
    (graph outage, missing provenance) is visible rather than reported as a clean zero."""
    from sqlalchemy import text

    diag = diag if diag is not None else {}
    cache: dict[str, tuple[str, str]] = {}
    entity_types = list(
        config.sender_entity_types or ("Person", "Organization", "Legal_Entity")
    )

    # (1) communication-origin entities
    resp = await arcade_client.execute_cypher(
        "MATCH (n) WHERE n.evidence_origin IN ['communication','hybrid'] "
        "RETURN n.grace_id AS gid, n.type AS type"
    )
    ent_rows = resp.get("result", []) if isinstance(resp, dict) else resp or []
    entities = [
        (str(r.get("gid") or r.get("grace_id")), r.get("type") or "")
        for r in ent_rows if (r.get("gid") or r.get("grace_id"))
    ]
    if not entities:
        return []
    gids = [e[0] for e in entities]

    # (2) BATCH provenance for ALL entities in one query (was N+1: one per entity).
    prov_by_gid: dict[str, list[str]] = {}
    try:
        prov = await arcade_client.execute_cypher(
            "MATCH (n)-[:produced_by]->(e:Extraction_Event) WHERE n.grace_id IN $gids "
            "RETURN n.grace_id AS gid, e.source_document_id AS sd",
            params={"gids": gids},
        )
        prov_rows = prov.get("result", []) if isinstance(prov, dict) else prov or []
    except Exception as exc:  # noqa: BLE001
        diag["errors_suppressed"] = diag.get("errors_suppressed", 0) + 1
        logger.warning("corroboration.provenance_query_error", error=str(exc))
        prov_rows = []
    for pr in prov_rows:
        gid = pr.get("gid") or pr.get("grace_id")
        sd = pr.get("sd") or ""
        if gid and sd.startswith("email:"):
            prov_by_gid.setdefault(str(gid), []).append(sd[len("email:"):])

    # (3) BATCH the Postgres sender/body lookup for ALL distinct message_ids (was E·M).
    # F2-07: also pull sender_display_name — the graph-fallback resolution
    # needle when the email itself matches no Person name/alias.
    all_mids = sorted({m for mids in prov_by_gid.values() for m in mids})
    comm_by_mid: dict[str, tuple[str, str, str | None]] = {}
    if all_mids:
        for row in session.execute(
            text(
                "SELECT message_id, sender_email, body_plain, sender_display_name "
                "FROM communication_events WHERE message_id = ANY(:mids)"
            ),
            {"mids": all_mids},
        ).fetchall():
            comm_by_mid[row[0]] = (row[1], row[2] or "", row[3])

    # (4) build corroborations from the maps; senders resolved via the memoized cache.
    corrs: list[EntityCorroboration] = []
    for gid, etype in entities:
        message_ids = prov_by_gid.get(gid, [])
        if not message_ids:
            diag["skipped_no_provenance"] = diag.get("skipped_no_provenance", 0) + 1
            continue
        mentions: list[SourceMention] = []
        for mid in message_ids:
            cm = comm_by_mid.get(mid)
            if cm is None:
                diag["skipped_no_comm_row"] = diag.get("skipped_no_comm_row", 0) + 1
                continue
            sender_email, body, display_name = cm
            person_id, category = await _resolve_sender(
                arcade_client, sender_email, entity_types, cache=cache, diag=diag,
                display_name=display_name,
            )
            stance = classify_stance(body, config)
            if stance != "contradict":
                # Mentioning an entity affirms its existence; only an explicit
                # contradiction cue flips the stance.
                stance = "affirm"
            mentions.append(
                SourceMention(
                    person_id=person_id, person_category=category, stance=stance,
                    quality_key="clear_assertion", message_id=mid,
                )
            )
        if mentions:
            corrs.append(
                EntityCorroboration(entity_grace_id=gid, entity_type=etype, mentions=mentions)
            )
    return corrs


def get_arcade_client():
    """Module-level indirection over the settings-aware shared factory (honors
    ``ARCADE_DATABASE`` — D538). Import is deferred to call time to keep the
    module import surface light; being a module attribute makes it patchable
    in tests (the D542 run-flow guard monkeypatches this name)."""
    from src.graph.arcade_client import get_arcade_client as _factory

    return _factory()


async def run_corroboration(*, dry_run: bool, config: CorroborationConfig) -> dict:
    """Query communication entities, score each, and promote those that clear the gate."""
    from src.shared.database import get_session_factory

    arcade = get_arcade_client()
    session = get_session_factory()()
    diag: dict[str, int] = {}
    promoted = provisional = 0
    try:
        corrs = await gather_communication_corroborations(arcade, session, config, diag=diag)
        # v2 (TruthFinder) must learn source reliability across ALL entities at once;
        # scoring per-entity reduces it to v1. Score the whole batch in one call.
        scores = (
            score_entities_v2(corrs, config) if config.iterative
            else [score_entity_v1(e, config) for e in corrs]
        )
        for entity, score in zip(corrs, scores):
            if score.status == "first_class":
                await promote_entity(
                    entity.entity_grace_id, score.status,
                    score.corroborating_sender_count, dry_run=dry_run,
                )
                promoted += 1
            else:
                provisional += 1
    finally:
        session.close()
        await arcade.aclose()
    return {"scored": len(corrs), "promoted": promoted, "provisional": provisional, **diag}


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for corroboration scoring."""
    # F-0049/ISS-0040: mirror this D246 subprocess's OTel metrics into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    import asyncio

    parser = _build_argparser()
    args = parser.parse_args(argv)

    if args.command != "run":
        parser.print_help()
        sys.exit(0)

    config = load_config()
    if args.v2:
        config.iterative = True

    logger.info(
        "corroboration.run_start",
        v2=config.iterative,
        dry_run=args.dry_run,
        observation_time=args.observation_time,
    )

    # D542: real run — query communication entities, score, promote.
    result = asyncio.run(run_corroboration(dry_run=args.dry_run, config=config))

    # Surface the full result incl. diagnostics (skipped_no_provenance,
    # unresolved_senders, errors_suppressed, ...) so a silently-degraded run is
    # distinguishable from a legitimate "nothing to promote".
    logger.info("corroboration.run_complete", dry_run=args.dry_run, **result)


if __name__ == "__main__":
    main()
