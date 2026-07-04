"""Decomposition pipeline orchestrator (Chunk 40, D310 + D315).

Wires Layer 1 → Layer 2 → Layer 3 → Layer 4 against a single
``decomposition_runs`` row and emits OTel spans + elicitation
EventTypes at lifecycle transitions.

Persistence pattern (D310):

* ``create_run()`` lands an INSERT with ``status='running'`` and NULL
  JSONB cells.
* Layer artifacts accumulate in memory.
* ``finalize_run()`` performs a single UPDATE at lifecycle close
  (``completed``, ``paused_pre_layer4``, or ``failed``); the
  append-only trigger permits JSONB writes only when the column was
  previously NULL — so this call works exactly once per row.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog
from opentelemetry import trace

from src.decomposition import run_repository
from src.decomposition.config import DecompositionConfig
from src.decomposition.layer1_walk import walk_archive
from src.decomposition.layer2_cluster import cluster_documents
from src.decomposition.layer3_cooccurrence import (
    build_cooccurrence_graph,
    extract_entities,
)
from src.decomposition.layer4_synthesize import synthesize_hypotheses
from src.decomposition.models import (
    Layer1Summary,
    Layer2Decision,
    Layer3Decision,
)
from src.decomposition.text_extractor import extract_text


log = structlog.get_logger()
_tracer = trace.get_tracer("grace.decomposition")


# ---------- Telemetry helpers ----------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _emit_event(
    event_type: str, payload: dict[str, Any], *, session=None
) -> None:
    """Best-effort elicitation event emit.

    Decomposition runs are not user-driven elicitation sessions, so the
    pipeline writes events through the same envelope schema but
    swallows DB errors — telemetry must never break the run.
    """
    try:  # pragma: no cover — best-effort path
        from src.elicitation.event_writer import write_event
        from src.elicitation.models import ElicitationEventEnvelope

        if session is None:
            return
        envelope = ElicitationEventEnvelope.model_validate(
            {
                "event_id": uuid4(),
                "event_type": event_type,
                "session_id": uuid4(),
                "actor_type": "system",
                "phase_name": "none",
                "emitted_at": _now(),
                "schema_version": 1,
                "grace_version": "phase5-chunk40",
                "payload": payload,
                "payload_schema_version": 1,
            }
        )
        write_event(session, envelope)
    except Exception:  # noqa: BLE001
        log.debug("decomposition.event_emit_failed", event_type=event_type)


def _emit_metric(name: str, archive_root_hash: str) -> None:
    """Increment a ``grace_decomposition_runs_*_total`` counter."""
    try:  # pragma: no cover — best-effort
        import importlib

        metrics = importlib.import_module("src.analytics.metrics")
        counter = getattr(metrics, name, None)
        if counter is None:
            return
        counter.add(1, {"archive_root_hash": archive_root_hash})
    except Exception:  # noqa: BLE001
        log.debug("decomposition.metric_emit_failed", name=name)


# ---------- Layer execution helpers ----------


async def _default_embed(texts: list[str]) -> list[list[float]]:
    # Phase-6 fix: ``embed_texts`` (Chunk 35a, D265) requires an explicit
    # ``base_url`` after the strangler-fig move to ``src.shared.embeddings``.
    # The orchestrator previously called it without one. Read the Ollama
    # base URL from GraceSettings so this honours operator overrides.
    from src.shared.config import get_settings
    from src.shared.embeddings import embed_texts

    settings = get_settings()
    return await embed_texts(texts, base_url=settings.ollama_base_url)


def _build_layer3_documents(
    layer1_summary,
    archive_root: Path,
) -> list[dict[str, Any]]:
    """Produce {"text": ...} dicts for the NER pass from the L1 inventory."""
    docs: list[dict[str, Any]] = []
    for entry in layer1_summary.files:
        full = archive_root / entry.relative_path
        try:
            extraction = extract_text(full)
        except Exception:  # noqa: BLE001
            continue
        if extraction.skipped or not extraction.body:
            continue
        text = extraction.body
        if extraction.title:
            text = f"{extraction.title}\n{text}"
        docs.append({"text": text})
    return docs


# ---------- Orchestrator ----------


async def run_decomposition(
    archive_root: Path,
    config: DecompositionConfig,
    db_session,
    embedding_provider=None,
    llm_provider=None,
    *,
    operator: UUID | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    run_id: UUID | None = None,
    rerun_direction: str | None = None,
) -> dict[str, Any]:
    """Run Layers 1–4 end-to-end against ``archive_root``.

    Returns the final ``decomposition_runs`` row. ``run_id`` may be
    supplied to adopt an existing row instead of INSERTing a new one:
    either the API trigger's placeholder row (F-030 / ISS-0014 — the
    route INSERTs first and passes ``--run-id`` so the polled id is the
    executing run, D460 pattern) or a resume successor row whose seeded
    Layer 1–3 artifacts are reused rather than recomputed.

    ``rerun_direction`` (ISS-0024): 'finer' | 'coarser' applies the
    documented ±1.5x Layer-3 Leiden resolution scaling for a rerun
    successor (D277 §3.4: reruns recompute L3+L4 at the new resolution).
    Direction is not persisted on the append-only row — the CLI argv is
    its transport; without this the successor recomputed Layer 3 at the
    SAME resolution and 'finer'/'coarser' was a label with no effect.
    """
    archive_root = Path(archive_root)
    embedding_provider = embedding_provider or _default_embed

    if rerun_direction is not None:
        if rerun_direction not in ("finer", "coarser"):
            raise ValueError(
                f"rerun_direction must be 'finer' or 'coarser' (got {rerun_direction!r})"
            )
        scale = 1.5 if rerun_direction == "finer" else (1.0 / 1.5)
        # Higher Leiden resolution -> more, smaller communities = finer.
        config = config.model_copy(deep=True)
        config.layer3.leiden.resolution = config.layer3.leiden.resolution * scale
        log.info(
            "decomposition.rerun_resolution_scaled",
            direction=rerun_direction,
            resolution=config.layer3.leiden.resolution,
        )

    layer1_artifact = None
    layer2_artifact = None
    layer3_artifact = None
    layer4_artifact = None
    # F-030 / ISS-0014: columns already persisted on an adopted row must be
    # excluded from every finalize_run() call — the D310 first-write-only
    # trigger rejects a second write of a non-NULL JSONB cell.
    preloaded_columns: set[str] = set()

    # ------- Lifecycle: create / adopt row -------
    if run_id is None:
        row = run_repository.create_run(
            db_session,
            archive_root=str(archive_root),
            operator=operator,
        )
    else:
        # F-030 / ISS-0014: adopt the existing row by id — either the API
        # trigger's placeholder (NULL JSONB cells; behaves exactly like a
        # fresh create) or a resume successor (Layers 1–3 seeded at INSERT).
        # Persisted artifacts are reused instead of recomputed, so the run
        # picks up where the row left off and finalize never collides with
        # the append-only trigger.
        existing = run_repository.get_run(db_session, run_id)
        if existing is None:
            raise ValueError(f"run_id {run_id} not found")
        row = existing
        if row.get("layer1_summary"):
            layer1_artifact = Layer1Summary.model_validate(row["layer1_summary"])
            preloaded_columns.add("layer1_summary")
        if row.get("layer2_decision"):
            layer2_artifact = Layer2Decision.model_validate(row["layer2_decision"])
            preloaded_columns.add("layer2_decision")
        if row.get("layer3_decision"):
            layer3_artifact = Layer3Decision.model_validate(row["layer3_decision"])
            preloaded_columns.add("layer3_decision")
        if row.get("total_documents") is not None:
            preloaded_columns.add("total_documents")

    def _fresh_artifacts(artifacts: dict[str, Any]) -> dict[str, Any]:
        """Drop preloaded columns from a finalize payload (F-030 / ISS-0014)."""
        return {
            k: v for k, v in artifacts.items() if k not in preloaded_columns
        }

    archive_hash = row["archive_root_canonical_hash"]
    new_run_id: UUID = row["run_id"]

    _emit_event(
        "decomposition_run_started",
        {
            "run_id": str(new_run_id),
            "archive_root_hash": archive_hash,
            "started_at": _now().isoformat(),
        },
        session=db_session,
    )
    _emit_metric("grace_decomposition_runs_started_total", archive_hash)

    with _tracer.start_as_current_span("decomposition.run") as run_span:
        run_span.set_attribute("decomposition.run_id", str(new_run_id))
        run_span.set_attribute("decomposition.archive_root_hash", archive_hash)
        run_span.set_attribute("decomposition.dry_run", dry_run)

        try:
            # Layer 1 — skipped when the adopted row already carries it
            # (F-030 / ISS-0014).
            if layer1_artifact is None:
                with _tracer.start_as_current_span("decomposition.layer1"):
                    layer1_artifact = walk_archive(archive_root, config)
                    if limit is not None and limit < layer1_artifact.total_files:
                        layer1_artifact = layer1_artifact.model_copy(
                            update={
                                "files": layer1_artifact.files[:limit],
                                "total_files": min(layer1_artifact.total_files, limit),
                            }
                        )

            if dry_run:
                run_repository.finalize_run(
                    db_session,
                    new_run_id,
                    status="completed",
                    completed_at=_now(),
                    layer_artifacts=_fresh_artifacts({
                        "layer1_summary": layer1_artifact,
                        "total_documents": layer1_artifact.total_files,
                    }),
                )
                db_session.commit()
                _emit_event(
                    "decomposition_run_completed",
                    {
                        "run_id": str(new_run_id),
                        "archive_root_hash": archive_hash,
                        "total_documents": layer1_artifact.total_files,
                        "completed_at": _now().isoformat(),
                    },
                    session=db_session,
                )
                _emit_metric(
                    "grace_decomposition_runs_completed_total", archive_hash
                )
                return run_repository.get_run(db_session, new_run_id) or {}

            # Layer 2 — skipped when preloaded (F-030 / ISS-0014).
            if layer2_artifact is None:
                with _tracer.start_as_current_span("decomposition.layer2"):
                    layer2_artifact = await cluster_documents(
                        file_inventory=layer1_artifact.files,
                        embedding_provider=embedding_provider,
                        config=config,
                        archive_root=archive_root,
                    )

            # Layer 3 — NER + cooccurrence graph; skipped when preloaded.
            if layer3_artifact is None:
                with _tracer.start_as_current_span("decomposition.layer3"):
                    ner_documents = _build_layer3_documents(
                        layer1_artifact, archive_root
                    )
                    entity_lists = await extract_entities(
                        ner_documents, llm_provider, config
                    )
                    layer3_artifact = build_cooccurrence_graph(entity_lists, config)

            # Layer 4 — synthesis (paused_pre_layer4 on validator failure)
            with _tracer.start_as_current_span("decomposition.layer4"):
                try:
                    layer4_artifact = await synthesize_hypotheses(
                        layer1_artifact,
                        layer2_artifact,
                        layer3_artifact,
                        llm_provider,
                        config,
                    )
                except Exception as exc:  # noqa: BLE001 — paused recovery
                    log.warning(
                        "decomposition.layer4_failed_pausing_run",
                        run_id=str(new_run_id),
                        error=str(exc),
                    )
                    run_repository.finalize_run(
                        db_session,
                        new_run_id,
                        status="paused_pre_layer4",
                        completed_at=_now(),
                        layer_artifacts=_fresh_artifacts({
                            "layer1_summary": layer1_artifact,
                            "layer2_decision": layer2_artifact,
                            "layer3_decision": layer3_artifact,
                            "total_documents": layer1_artifact.total_files,
                        }),
                    )
                    db_session.commit()
                    _emit_event(
                        "decomposition_run_failed",
                        {
                            "run_id": str(new_run_id),
                            "archive_root_hash": archive_hash,
                            "error_summary": f"layer4_paused: {exc}",
                            "failed_at": _now().isoformat(),
                        },
                        session=db_session,
                    )
                    _emit_metric(
                        "grace_decomposition_runs_failed_total", archive_hash
                    )
                    return run_repository.get_run(db_session, new_run_id) or {}

            # Happy-path completion.
            run_repository.finalize_run(
                db_session,
                new_run_id,
                status="completed",
                completed_at=_now(),
                layer_artifacts=_fresh_artifacts({
                    "layer1_summary": layer1_artifact,
                    "layer2_decision": layer2_artifact,
                    "layer3_decision": layer3_artifact,
                    "layer4_hypotheses": layer4_artifact,
                    "total_documents": layer1_artifact.total_files,
                }),
            )
            db_session.commit()
            _emit_event(
                "decomposition_run_completed",
                {
                    "run_id": str(new_run_id),
                    "archive_root_hash": archive_hash,
                    "total_documents": layer1_artifact.total_files,
                    "completed_at": _now().isoformat(),
                },
                session=db_session,
            )
            _emit_metric(
                "grace_decomposition_runs_completed_total", archive_hash
            )
            return run_repository.get_run(db_session, new_run_id) or {}

        except Exception as exc:  # noqa: BLE001 — unexpected failure
            log.error(
                "decomposition.run_failed",
                run_id=str(new_run_id),
                error=str(exc),
            )
            try:
                run_repository.finalize_run(
                    db_session,
                    new_run_id,
                    status="failed",
                    completed_at=_now(),
                    layer_artifacts=_fresh_artifacts({
                        "layer1_summary": layer1_artifact,
                        "layer2_decision": layer2_artifact,
                        "layer3_decision": layer3_artifact,
                    }),
                )
                db_session.commit()
            except Exception:  # noqa: BLE001
                db_session.rollback()
            _emit_event(
                "decomposition_run_failed",
                {
                    "run_id": str(new_run_id),
                    "archive_root_hash": archive_hash,
                    "error_summary": str(exc),
                    "failed_at": _now().isoformat(),
                },
                session=db_session,
            )
            _emit_metric("grace_decomposition_runs_failed_total", archive_hash)
            raise
