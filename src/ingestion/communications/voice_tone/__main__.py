"""CLI dispatch for Voice & Tone Profiling Engine (Chunk 58, D246 mirror).

Subcommands: ``run``, ``archive``, ``erase``, ``export``.

Usage::

    python -m src.ingestion.communications.voice_tone run [--dry-run] [--operator <handle>]
    python -m src.ingestion.communications.voice_tone archive [--dry-run]
    python -m src.ingestion.communications.voice_tone erase --person-email <email>
    python -m src.ingestion.communications.voice_tone export --person <id|email> --format markdown
"""

from __future__ import annotations

import argparse
import json
import sys


def main() -> None:
    # F-15: mirror this subprocess's OTel counters into the prometheus
    # multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    parser = argparse.ArgumentParser(
        prog="voice_tone",
        description="Voice & Tone Profiling Engine CLI (D246 mirror)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    run_parser = sub.add_parser("run", help="Generate communication style profiles")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--operator", type=str, default=None)

    # archive
    archive_parser = sub.add_parser("archive", help="Archive tier scan")
    archive_parser.add_argument("--dry-run", action="store_true")

    # erase
    erase_parser = sub.add_parser("erase", help="Erase all profile data for a person")
    erase_parser.add_argument("--person-email", required=True, type=str)

    # export (Chunk 78, D505 — Voice Card v1)
    export_parser = sub.add_parser("export", help="Export a Voice Card")
    export_group = export_parser.add_mutually_exclusive_group(required=True)
    export_group.add_argument("--person", type=str, help="Person ID or email")
    export_group.add_argument("--segment", type=str, help="Segment name")
    export_parser.add_argument(
        "--format",
        choices=["markdown", "claude-skill", "claude-style", "json"],
        default="markdown",
        dest="export_format",
    )
    export_parser.add_argument("--recipient", type=str, default=None)
    export_parser.add_argument("--category", type=str, default=None)
    export_parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output directory, or an exact output file when the path has a file suffix",
    )

    args = parser.parse_args()

    if args.command == "run":
        from src.ingestion.communications.voice_tone.profile_generator import (
            run_profile_generation,
        )

        result = run_profile_generation(dry_run=args.dry_run, operator=args.operator)
        print(json.dumps(result, indent=2))

    elif args.command == "archive":
        from src.ingestion.communications.voice_tone.profile_generator import (
            run_archive_scan,
        )

        result = run_archive_scan(dry_run=args.dry_run)
        print(json.dumps(result, indent=2))

    elif args.command == "erase":
        from src.ingestion.communications.voice_tone.profile_generator import (
            run_erase,
        )

        result = run_erase(person_email=args.person_email)
        print(json.dumps(result, indent=2))

    elif args.command == "export":
        import pathlib

        from src.ingestion.communications.voice_tone.voice_card import (
            VoiceCardRenderer,
        )
        from src.ingestion.communications.voice_tone.models import (
            StyleSignature,
            VoiceToneConfig,
        )

        import yaml

        config_path = pathlib.Path("config/voice_tone_config.yaml")
        if config_path.exists():
            config = VoiceToneConfig(**yaml.safe_load(config_path.read_text()) or {})
        else:
            config = VoiceToneConfig()

        subject = args.person or args.segment or "unknown"

        # F-0033c / ISS-0049 (validation run 2026-07-03): ``--out
        # report.md`` was treated as a directory, writing
        # report.md/<subject>/voice-card.<ext>. When the --out argument
        # carries a file suffix, honor it as the exact output file;
        # directory arguments keep the historical <out>/<subject>/ layout.
        out_arg = pathlib.Path(args.out) if args.out else None
        out_file: pathlib.Path | None = None
        if out_arg is not None and out_arg.suffix and not out_arg.is_dir():
            out_file = out_arg
            out_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            out_dir = (out_arg or pathlib.Path(config.export_default_dir)) / subject
            out_dir.mkdir(parents=True, exist_ok=True)

        # Read profile from DB.
        # F-34 (validation run, 2026-07-01): this path shipped importing
        # nonexistent ``src.shared.db`` and querying a nonexistent ``person_id``
        # column with the raw subject string — export was unreachable in
        # production. Resolve email subjects through entity_resolution_registry
        # and read ``sender_person_id`` (the real column).
        from src.shared.database import get_session_factory

        with get_session_factory()() as session:
            from sqlalchemy import text

            pid = subject
            if args.person and "@" in subject:
                reg = session.execute(
                    text(
                        "SELECT canonical_grace_id FROM entity_resolution_registry "
                        "WHERE canonical_name = :em AND canonical_type = 'Person' LIMIT 1"
                    ),
                    {"em": subject},
                ).first()
                if reg is not None:
                    pid = str(reg[0])
                else:
                    # F-0033b / ISS-0049 (validation run 2026-07-03): the
                    # registry is only populated by connector/federation code,
                    # so on most deployments the email fell through unresolved
                    # and CAST(:pid AS uuid) 500'd. Reuse the F-31 graph-based
                    # sender lookup (role_resolver.resolve_sender_person — the
                    # email→Person path profile generation itself uses) before
                    # giving up.
                    import asyncio

                    from src.ingestion.communications.voice_tone.role_resolver import (
                        resolve_sender_person,
                    )

                    try:
                        resolved = asyncio.run(resolve_sender_person(subject))
                    except Exception:
                        resolved = None
                    if resolved:
                        pid = str(resolved)

            if args.segment:
                # Aggregate profiles are keyed by aggregate_segment, not
                # sender_person_id (c58a XOR constraint) — never CAST a
                # segment name to uuid.
                row = session.execute(
                    text(
                        "SELECT style_signature, profile_version "
                        "FROM communication_style_profiles "
                        "WHERE aggregate_segment = :seg "
                        "ORDER BY profile_version DESC LIMIT 1"
                    ),
                    {"seg": subject},
                ).first()
            else:
                # F-0033b / ISS-0049: guard the uuid CAST — an unresolvable
                # subject now exits cleanly with guidance instead of a raw
                # psycopg2 InvalidTextRepresentation crash.
                import uuid as _uuid

                try:
                    _uuid.UUID(pid)
                except (ValueError, AttributeError, TypeError):
                    print(
                        json.dumps(
                            {
                                "error": (
                                    f"Could not resolve '{subject}' to a Person id — "
                                    "no entity_resolution_registry row and no graph "
                                    "Person matched. Pass the person UUID directly, "
                                    "or run profile generation first."
                                )
                            }
                        )
                    )
                    sys.exit(1)

                row = session.execute(
                    text(
                        "SELECT style_signature, profile_version "
                        "FROM communication_style_profiles "
                        "WHERE sender_person_id = CAST(:pid AS uuid) "
                        "ORDER BY profile_version DESC LIMIT 1"
                    ),
                    {"pid": pid},
                ).first()

            if row is None:
                print(json.dumps({"error": f"No profile found for {subject}"}))
                sys.exit(1)

            sig_data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            sig = StyleSignature.model_validate(sig_data)
            source_count = sig_data.get("_source_email_count", 0)

            # F-0033 rider / ISS-0049: the signature JSON never carried
            # ``_source_email_count``, so exported cards claimed
            # ``source_email_count: 0`` despite real source emails. When the
            # subject is an email, count its communication_events rows at
            # export time (best-effort — count stays as-is on any failure).
            if not source_count and args.person and "@" in subject:
                try:
                    cnt_row = session.execute(
                        text(
                            "SELECT COUNT(*) FROM communication_events "
                            "WHERE sender_email = :em"
                        ),
                        {"em": subject},
                    ).first()
                    if cnt_row and cnt_row[0]:
                        source_count = int(cnt_row[0])
                except Exception:
                    pass

        renderer = VoiceCardRenderer(word_limit=config.voice_card_core_word_limit)
        rendered = renderer.render(
            profile=sig,
            subject=subject,
            fmt=args.export_format,
            source_email_count=source_count,
        )

        ext_map = {
            "markdown": "md",
            "claude-skill": "md",
            "claude-style": "txt",
            "json": "json",
        }
        # F-0033c / ISS-0049: exact-file --out wins; directory mode keeps the
        # historical <dir>/<subject>/voice-card.<ext> layout.
        if out_file is not None:
            out_path = out_file
        else:
            filename = f"voice-card.{ext_map[args.export_format]}"
            out_path = out_dir / filename
        out_path.write_text(rendered, encoding="utf-8")

        # F-36 (validation run, 2026-07-01): wire the D505/D506 export audit
        # trail + counter. The export handler previously returned before any
        # audit persistence, so voice_card_exports stayed empty after a
        # successful export and grace_voice_cards_exported_total never bumped.
        from src.ingestion.communications.voice_tone.voice_card import (
            record_export_audit,
        )

        record_export_audit(
            subject=subject,
            profile_version=int(row[1]) if row[1] is not None else 0,
            fmt=args.export_format,
            redaction_applied=config.redaction_enabled,
            operator=getattr(args, "operator", None),
        )

        print(json.dumps({"exported": str(out_path), "format": args.export_format}))

    sys.exit(0)


if __name__ == "__main__":
    main()
