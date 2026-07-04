"""F-030 / ISS-0014 regression: ``run_id`` adoption of an existing row.

``POST /api/decomposition/runs/trigger`` INSERTs a placeholder row and
passes ``--run-id`` to the CLI; the orchestrator must adopt that exact
row (no second INSERT) so the id the operator polls IS the executing
run. Resume successor rows carry Layers 1–3 seeded at INSERT — those
columns must be reused, not recomputed, and must be excluded from the
finalize UPDATE (the D310 first-write-only trigger rejects a second
write of a non-NULL JSONB cell).

Session and repository are mocked (test_cli.py convention) — no DB.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from src.decomposition.config import DecompositionConfig
from src.decomposition.pipeline.orchestrator import run_decomposition


_HASH = "ab" * 32


def _row(run_id: UUID, archive_root: Path, **overrides) -> dict:
    row = {
        "run_id": run_id,
        "archive_root": str(archive_root),
        "archive_root_canonical_hash": _HASH,
        "status": "running",
        "layer1_summary": None,
        "layer2_decision": None,
        "layer3_decision": None,
        "layer4_hypotheses": None,
        "total_documents": None,
    }
    row.update(overrides)
    return row


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    (root / "memo.txt").write_text("Ops memo. Acme Inc discusses vendor terms.")
    return root


def test_run_id_adopts_placeholder_row(archive: Path):
    """A placeholder row (NULL JSONB) is adopted — no second INSERT."""
    rid = uuid4()
    session = MagicMock()

    with patch(
        "src.decomposition.pipeline.orchestrator.run_repository"
    ) as repo:
        repo.get_run.side_effect = [
            _row(rid, archive),
            {"run_id": rid, "status": "completed"},
        ]
        result = asyncio.run(
            run_decomposition(
                archive_root=archive,
                config=DecompositionConfig(),
                db_session=session,
                dry_run=True,
                run_id=rid,
            )
        )

    # F-030 / ISS-0014: the placeholder is reused, never re-INSERTed.
    repo.create_run.assert_not_called()
    assert repo.finalize_run.call_args.args[1] == rid
    kwargs = repo.finalize_run.call_args.kwargs
    assert kwargs["status"] == "completed"
    # Placeholder had NULL cells, so Layer 1 is freshly written.
    assert "layer1_summary" in kwargs["layer_artifacts"]
    assert result["status"] == "completed"


def test_preloaded_artifacts_are_skipped_and_not_rewritten(archive: Path):
    """Seeded Layer 1 (resume successor) is reused, not recomputed, and is
    excluded from the finalize payload (D310 first-write-only trigger)."""
    rid = uuid4()
    session = MagicMock()
    seeded_layer1 = {
        "archive_root": str(archive),
        "total_files": 1,
        "files": [],
        "folders": [],
    }

    with patch(
        "src.decomposition.pipeline.orchestrator.run_repository"
    ) as repo, patch(
        "src.decomposition.pipeline.orchestrator.walk_archive"
    ) as walk:
        repo.get_run.side_effect = [
            _row(rid, archive, layer1_summary=seeded_layer1, total_documents=1),
            {"run_id": rid, "status": "completed"},
        ]
        asyncio.run(
            run_decomposition(
                archive_root=archive,
                config=DecompositionConfig(),
                db_session=session,
                dry_run=True,
                run_id=rid,
            )
        )

    walk.assert_not_called()
    kwargs = repo.finalize_run.call_args.kwargs
    # F-030 / ISS-0014: preloaded columns filtered — re-writing them would
    # trip the append-only trigger's first-write-only guard.
    assert "layer1_summary" not in kwargs["layer_artifacts"]
    assert "total_documents" not in kwargs["layer_artifacts"]


def test_unknown_run_id_raises(archive: Path):
    """Adopting a nonexistent row fails loudly instead of forking a new run."""
    session = MagicMock()
    with patch(
        "src.decomposition.pipeline.orchestrator.run_repository"
    ) as repo:
        repo.get_run.return_value = None
        with pytest.raises(ValueError, match="not found"):
            asyncio.run(
                run_decomposition(
                    archive_root=archive,
                    config=DecompositionConfig(),
                    db_session=session,
                    dry_run=True,
                    run_id=uuid4(),
                )
            )
    repo.create_run.assert_not_called()


class TestRerunDirectionScaling:
    """ISS-0024: --rerun-direction applies the documented ±1.5x Layer-3
    Leiden resolution scaling (previously the direction was a label with
    no effect — the successor recomputed Layer 3 at the SAME resolution)."""

    def _base_config(self):
        from src.decomposition.config import DecompositionConfig

        return DecompositionConfig()

    @pytest.mark.asyncio
    async def test_finer_scales_resolution_up(self, monkeypatch):
        from src.decomposition.pipeline import orchestrator

        cfg = self._base_config()
        base = cfg.layer3.leiden.resolution
        seen = {}

        def _capture_create(session, **kwargs):
            raise RuntimeError("stop-after-config")  # halt before any layer work

        monkeypatch.setattr(
            orchestrator.run_repository, "create_run", _capture_create
        )
        # Wrap: capture the (copied) config's resolution at the earliest hook.
        orig_log = orchestrator.log.info

        def _spy(event, **kw):
            if event == "decomposition.rerun_resolution_scaled":
                seen["resolution"] = kw["resolution"]
            return orig_log(event, **kw)

        monkeypatch.setattr(orchestrator.log, "info", _spy)
        with pytest.raises(RuntimeError, match="stop-after-config"):
            await orchestrator.run_decomposition(
                archive_root=Path("/nonexistent"),
                config=cfg,
                db_session=None,
                rerun_direction="finer",
            )
        assert seen["resolution"] == pytest.approx(base * 1.5)
        # The caller's config object is untouched (deep copy).
        assert cfg.layer3.leiden.resolution == base

    @pytest.mark.asyncio
    async def test_coarser_scales_resolution_down(self, monkeypatch):
        from src.decomposition.pipeline import orchestrator

        cfg = self._base_config()
        base = cfg.layer3.leiden.resolution
        seen = {}
        monkeypatch.setattr(
            orchestrator.run_repository,
            "create_run",
            lambda session, **kw: (_ for _ in ()).throw(RuntimeError("stop")),
        )
        orig_log = orchestrator.log.info

        def _spy(event, **kw):
            if event == "decomposition.rerun_resolution_scaled":
                seen["resolution"] = kw["resolution"]
            return orig_log(event, **kw)

        monkeypatch.setattr(orchestrator.log, "info", _spy)
        with pytest.raises(RuntimeError, match="stop"):
            await orchestrator.run_decomposition(
                archive_root=Path("/nonexistent"),
                config=cfg,
                db_session=None,
                rerun_direction="coarser",
            )
        assert seen["resolution"] == pytest.approx(base / 1.5)

    @pytest.mark.asyncio
    async def test_invalid_direction_rejected(self):
        from src.decomposition.pipeline import orchestrator

        with pytest.raises(ValueError, match="finer.*coarser|coarser.*finer"):
            await orchestrator.run_decomposition(
                archive_root=Path("/nonexistent"),
                config=self._base_config(),
                db_session=None,
                rerun_direction="sideways",
            )
