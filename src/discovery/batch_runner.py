"""Sync batch runner: scan directory or read manifest, process all documents."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import structlog  # noqa: E402

from src.discovery.database import (  # noqa: E402
    create_document,
    get_document_by_path,
    get_processing_summary,
    update_document,
)
from src.discovery.document_processor import _build_converter, process_document  # noqa: E402
from src.discovery.metadata_tagger import tag_document  # noqa: E402
from src.discovery.models import load_discovery_config  # noqa: E402
from src.shared.config import get_settings  # noqa: E402
from src.shared.database import get_db  # noqa: E402

logger = structlog.get_logger()


def _collect_files_from_dir(source_dir: Path) -> list[Path]:
    """Recursively collect files with supported extensions from a directory."""
    config = load_discovery_config()
    supported = set(config["supported_extensions"])
    files = []
    if not source_dir.is_dir():
        logger.warning("source_dir_not_found", path=str(source_dir))
        return files
    for f in sorted(source_dir.rglob("*")):
        if f.is_file() and f.suffix.lower() in supported:
            files.append(f.resolve())
    return files


def _collect_files_from_manifest(manifest_path: Path) -> list[Path]:
    """Read file paths from a discovery manifest JSON."""
    with open(manifest_path) as f:
        manifest = json.load(f)
    return [Path(p).resolve() for p in manifest.get("files", [])]


def run_batch(
    manifest_path: Path | None = None,
    source_dir: Path | None = None,
    dry_run: bool = False,
    reprocess: bool = False,
) -> dict:
    """Process all documents in the manifest or source directory.

    Steps:
    1. Determine file list (from manifest or directory scan)
    2. Create DocumentConverter once (reuse across all files)
    3. For each file: check duplicates, process, tag, store
    4. Return processing summary
    """
    # Determine file list
    if manifest_path and manifest_path.exists():
        files = _collect_files_from_manifest(manifest_path)
        logger.info("batch_source", source="manifest", file_count=len(files))
    elif source_dir:
        files = _collect_files_from_dir(source_dir)
        logger.info("batch_source", source="directory", path=str(source_dir), file_count=len(files))
    else:
        # Default: try manifest, then source dir from settings
        default_manifest = Path("config/discovery-manifest.json")
        if default_manifest.exists():
            files = _collect_files_from_manifest(default_manifest)
            logger.info("batch_source", source="default_manifest", file_count=len(files))
        else:
            settings = get_settings()
            files = _collect_files_from_dir(Path(settings.discovery_source_dir))
            logger.info("batch_source", source="settings_dir", file_count=len(files))

    if dry_run:
        logger.info("dry_run_summary", total_files=len(files))
        for f in files:
            logger.info("dry_run_file", file=str(f))
        return {"dry_run": True, "total_files": len(files), "files": [str(f) for f in files]}

    # Create converter once for the batch — honors document_processing.pipeline_mode (D443).
    config = load_discovery_config()
    converter = _build_converter(config)

    db_gen = get_db()
    db = next(db_gen)

    processed = 0
    skipped_dup = 0
    total = len(files)

    try:
        for i, file_path in enumerate(files, 1):
            # Skip duplicates (unless --reprocess overrides)
            existing = get_document_by_path(db, str(file_path))
            if existing is not None and not reprocess:
                logger.info(
                    "batch_skip_duplicate",
                    file=str(file_path),
                    index=f"{i}/{total}",
                )
                skipped_dup += 1
                continue

            start = time.monotonic()
            doc = process_document(file_path, converter=converter)
            doc = tag_document(doc, file_path)

            if existing is not None and reprocess:
                # UPDATE existing row in place (Chunk 62, CP5, D443).
                update_document(db, str(file_path), doc)
                logger.info(
                    "batch_reprocessed",
                    file=doc.file_name,
                    status=doc.status.value,
                    index=f"{i}/{total}",
                )
            else:
                create_document(db, doc)

            elapsed = time.monotonic() - start

            logger.info(
                "batch_processed",
                file=doc.file_name,
                status=doc.status.value,
                word_count=doc.word_count,
                elapsed_seconds=round(elapsed, 1),
                index=f"{i}/{total}",
            )
            processed += 1

        summary = get_processing_summary(db)
        logger.info("batch_complete", processed=processed, skipped_duplicates=skipped_dup, summary=summary)
        return summary

    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass


# D476: argparser factory for CLI argv contract testing. Authorization: D476.
def _build_argparser() -> argparse.ArgumentParser:
    """Return the CLI argparser without parsing argv.

    Extracted from ``main()`` so that ``tests/api/test_route_cli_spawn_contracts.py``
    can validate route-side argv construction against this parser.
    """
    parser = argparse.ArgumentParser(description="GrACE Discovery batch document processor")
    parser.add_argument("--source-dir", type=str, help="Directory to scan for documents")
    parser.add_argument("--manifest", type=str, help="Path to discovery manifest JSON")
    parser.add_argument("--dry-run", action="store_true", help="List files without processing")
    parser.add_argument(
        "--reprocess", action="store_true",
        help="Re-run Docling from source files and UPDATE existing rows in place (D443)",
    )
    parser.add_argument(
        "--job-id", type=str, default=None,
        help="extraction_jobs UUID — when present, report progress back to the DB row",
    )
    parser.add_argument(
        "--router-strategy", type=str, choices=["sensitivity", "size_tier"],
        help="Per-document routing strategy; omit for single-provider serial",
    )
    return parser


def main() -> None:
    """CLI entry point for the batch runner."""
    parser = _build_argparser()
    args = parser.parse_args()

    manifest_path = Path(args.manifest) if args.manifest else None
    source_dir = Path(args.source_dir) if args.source_dir else None

    if args.job_id and args.router_strategy:
        _run_with_router(args, source_dir)
    elif args.job_id:
        _run_with_job_tracking(args, manifest_path, source_dir)
    else:
        result = run_batch(
            manifest_path=manifest_path,
            source_dir=source_dir,
            dry_run=args.dry_run,
            reprocess=args.reprocess,
        )

        if args.dry_run:
            print(f"\nDry run: {result['total_files']} files would be processed")
        else:
            print(f"\nProcessing summary: {json.dumps(result, indent=2)}")


def _run_with_router(args, source_dir) -> None:
    """Route documents into shards and spawn one subprocess per shard (D471).

    Requires ``--job-id`` and ``--router-strategy``. Populates
    ``extraction_jobs.shard_pids`` JSONB. Registers SIGINT/SIGTERM handler
    that kills shard process groups and cleans up temp directories.
    """
    from datetime import datetime, timezone

    from sqlalchemy import text as sa_text

    from src.extraction.router import RouterStrategy, route, stage_shard_directory, validate_strategy_implemented
    from src.extraction.router_config import load_router_config
    from src.shared.database import get_session_factory

    strategy = RouterStrategy(args.router_strategy)
    validate_strategy_implemented(strategy)

    config = load_router_config()
    job_id = args.job_id

    factory = get_session_factory()
    session = factory()

    # Collect files
    if source_dir:
        files = _collect_files_from_dir(source_dir)
    else:
        settings = get_settings()
        files = _collect_files_from_dir(Path(settings.discovery_source_dir))

    if not files:
        logger.warning("router.no_files", job_id=job_id)
        session.execute(
            sa_text("UPDATE extraction_jobs SET status='completed', completed_at=now() WHERE job_id=:jid"),
            {"jid": job_id},
        )
        session.commit()
        session.close()
        return

    # Route into shards
    shards = route(files, config, strategy)
    logger.info("router.shards_created", job_id=job_id, shard_count=len(shards), strategy=strategy.value)

    # Create parent temp directory for staged shard dirs
    parent_dir = Path(tempfile.mkdtemp(prefix="grace_shard_"))

    # Stage shard directories and spawn subprocesses
    procs: list[subprocess.Popen] = []
    schema_path = Path(__file__).resolve().parents[1].parent / "config" / "discovery.yaml"

    try:
        for shard in shards:
            staged_dir = stage_shard_directory(shard, parent_dir)
            cmd = [
                sys.executable, "-m", "src.extraction.eval_checkpoint",
                "--schema", str(schema_path),
                "--doc-dir", str(staged_dir),
                "--provider", shard.provider,
                "--model", shard.model,
                "--job-id", str(job_id),
            ]
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                cwd=str(Path(__file__).resolve().parents[2]),
            )
            procs.append(proc)
            logger.info(
                "router.shard_spawned",
                job_id=job_id,
                provider=shard.provider,
                pid=proc.pid,
                file_count=len(shard.source_paths),
            )
    except Exception as exc:
        # Clean up any already-spawned processes
        for p in procs:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        shutil.rmtree(parent_dir, ignore_errors=True)
        session.execute(
            sa_text("UPDATE extraction_jobs SET status='failed', completed_at=now(), error_message=:err WHERE job_id=:jid"),
            {"jid": job_id, "err": str(exc)[:500]},
        )
        session.commit()
        session.close()
        raise

    # Record shard PIDs
    shard_pids = [p.pid for p in procs]
    now_iso = datetime.now(timezone.utc).isoformat()
    session.execute(
        sa_text(
            "UPDATE extraction_jobs SET status='running', started_at=now(), "
            "shard_pids=:sp, progress_json=:pj WHERE job_id=:jid"
        ),
        {
            "jid": job_id,
            "sp": json.dumps(shard_pids),
            "pj": json.dumps({
                "strategy": strategy.value,
                "shard_count": len(shards),
                "shard_pids": shard_pids,
                "last_tick_at": now_iso,
            }),
        },
    )
    session.commit()

    # Register SIGINT/SIGTERM handler for clean shard termination
    _original_sigint = signal.getsignal(signal.SIGINT)
    _original_sigterm = signal.getsignal(signal.SIGTERM)

    def _cleanup_handler(signum, frame):
        logger.info("router.signal_received", signal=signum, job_id=job_id)
        for p in procs:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        # Wait 5 seconds then escalate
        time.sleep(5)
        for p in procs:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        shutil.rmtree(parent_dir, ignore_errors=True)
        session.execute(
            sa_text("UPDATE extraction_jobs SET status='cancelled', completed_at=now() WHERE job_id=:jid"),
            {"jid": job_id},
        )
        session.commit()
        session.close()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, _cleanup_handler)
    signal.signal(signal.SIGTERM, _cleanup_handler)

    try:
        # Wait for all shard subprocesses to complete
        for p in procs:
            p.wait()

        # Aggregate completion
        now_iso = datetime.now(timezone.utc).isoformat()
        session.execute(
            sa_text(
                "UPDATE extraction_jobs SET status='completed', completed_at=now(), "
                "progress_json=:pj WHERE job_id=:jid"
            ),
            {
                "jid": job_id,
                "pj": json.dumps({
                    "strategy": strategy.value,
                    "shard_count": len(shards),
                    "shard_pids": shard_pids,
                    "all_completed": True,
                    "last_tick_at": now_iso,
                }),
            },
        )
        session.commit()
        logger.info("router.all_shards_complete", job_id=job_id)

        # OTel emit (best-effort)
        try:
            from src.analytics.metrics import record_extraction_job_completed
            record_extraction_job_completed(job_kind="batch")
        except Exception:  # noqa: BLE001
            pass
    except SystemExit:
        raise
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()[:500]
        session.execute(
            sa_text(
                "UPDATE extraction_jobs SET status='failed', completed_at=now(), "
                "error_message=:err WHERE job_id=:jid"
            ),
            {"jid": job_id, "err": tb},
        )
        session.commit()
        try:
            from src.analytics.metrics import record_extraction_job_failed
            record_extraction_job_failed(job_kind="batch")
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        # Restore original signal handlers
        signal.signal(signal.SIGINT, _original_sigint)
        signal.signal(signal.SIGTERM, _original_sigterm)
        # Clean up temp directory
        shutil.rmtree(parent_dir, ignore_errors=True)
        session.close()


def _run_with_job_tracking(args, manifest_path, source_dir) -> None:
    """Wrap run_batch with extraction_jobs progress reporting (D470)."""
    from datetime import datetime, timezone

    from sqlalchemy import text as sa_text

    from src.shared.database import get_session_factory

    factory = get_session_factory()
    session = factory()
    job_id = args.job_id

    try:
        # Mark running
        now_iso = datetime.now(timezone.utc).isoformat()
        session.execute(
            sa_text(
                "UPDATE extraction_jobs SET status='running', started_at=now(), "
                "progress_json=:pj WHERE job_id=:jid"
            ),
            {"jid": job_id, "pj": json.dumps({"documents_processed": 0, "documents_total": 0, "current_file": "", "last_tick_at": now_iso})},
        )
        session.commit()

        result = run_batch(
            manifest_path=manifest_path,
            source_dir=source_dir,
            dry_run=args.dry_run,
            reprocess=args.reprocess,
        )

        # Mark completed
        now_iso = datetime.now(timezone.utc).isoformat()
        session.execute(
            sa_text(
                "UPDATE extraction_jobs SET status='completed', completed_at=now(), "
                "progress_json=:pj WHERE job_id=:jid"
            ),
            {"jid": job_id, "pj": json.dumps({"documents_processed": result.get("total_processed", 0), "documents_total": result.get("total_files", 0), "current_file": "done", "last_tick_at": now_iso})},
        )
        session.commit()

        if args.dry_run:
            print(f"\nDry run: {result['total_files']} files would be processed")
        else:
            print(f"\nProcessing summary: {json.dumps(result, indent=2)}")

        # OTel emit (best-effort)
        try:
            from src.analytics.metrics import record_extraction_job_completed
            record_extraction_job_completed(job_kind="batch")
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:
        import traceback

        # D475: capture up to 4096 chars of traceback for operator visibility
        tb = traceback.format_exc()[-4096:]
        try:
            session.execute(
                sa_text(
                    "UPDATE extraction_jobs SET status='failed', completed_at=now(), "
                    "error_message=:err WHERE job_id=:jid"
                ),
                {"jid": job_id, "err": tb},
            )
            session.commit()
        except Exception:  # noqa: BLE001 — D460 pattern: don't mask original error
            logger.warning("batch_runner.job_tracking.update_failed", exc_info=True)

        # OTel emit (best-effort)
        try:
            from src.analytics.metrics import record_extraction_job_failed
            record_extraction_job_failed(job_kind="batch")
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
