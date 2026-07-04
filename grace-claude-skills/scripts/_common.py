"""Shared bootstrap for the grace-claude-skills helper scripts.

These scripts run OUTSIDE the grace repo but import grace's own models so the
rows/payloads they write are schema-correct. We chdir into the repo root so that
pydantic-settings (.env) and the relative config/discovery.yaml load resolve, and
add the repo root to sys.path so `import src.*` works.

No LLM / Ollama calls happen here. DB reads/writes only.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# F-007 (validation-run ledger, 2026-07-02): default to THIS checkout — the repo
# containing this file — never a guessed ~/grace. With two grace checkouts on
# one machine, the old guess chdir'd helpers into the OTHER checkout and ran
# its code + .env (the harness silently executed unfixed embed_texts; only
# exported env vars kept the sandbox rail intact). Explicit arg / GRACE_ROOT
# still override for CI portability.
DEFAULT_GRACE_ROOT = str(Path(__file__).resolve().parents[2])


def route_logs_to_stderr(quiet: bool = False) -> None:
    """Send grace's structlog output to stderr so CLI helpers can emit clean JSON
    on stdout. Grace configures structlog with a stdout PrintLogger on import; its
    ``arcade.query`` info lines otherwise interleave with a helper's JSON payload and
    break downstream `json.load`. Call AFTER grace modules are imported (last
    `structlog.configure` wins). Best-effort — never fatal.

    R6 (session-4): ``quiet=True`` additionally raises the log floor to WARNING (so the
    ~20-40 per-call ``arcade.query`` INFO lines stop entirely — the unanimous cold-start
    friction) and silences Pydantic ``UserWarning`` noise. This is the default for the
    probe CLIs; pass ``--verbose`` to restore full INFO logs.
    """
    try:
        import structlog  # noqa: E402

        cfg = structlog.get_config()
        kwargs = dict(
            processors=cfg.get("processors"),
            logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        )
        if quiet:
            import logging
            import warnings

            warnings.filterwarnings("ignore", category=UserWarning)
            kwargs["wrapper_class"] = structlog.make_filtering_bound_logger(logging.WARNING)
            logging.getLogger().setLevel(logging.WARNING)
        structlog.configure(**kwargs)
    except Exception:  # pragma: no cover - logging is non-critical
        pass


def add_grace_to_path(grace_root: str | None = None) -> Path:
    """Resolve the repo root, chdir into it, and put it on sys.path.

    chdir matters: GraceSettings reads `.env` and config loaders read
    `config/discovery.yaml` via paths relative to the current working directory.
    """
    # GRACE_ROOT env fallback (CI portability; matches the repo's shell-helper convention)
    # so the harness works when the checkout is not at ~/grace.
    root = Path(grace_root or os.environ.get("GRACE_ROOT") or DEFAULT_GRACE_ROOT).expanduser().resolve()
    if not (root / "src").is_dir():
        raise SystemExit(f"[grace-claude-skills] grace root not found at {root} (no src/).")
    # F-007: always announce which checkout we execute against — the silent
    # wrong-checkout mode is otherwise indistinguishable from a correct run.
    print(f"[grace-claude-skills] grace root: {root}", file=sys.stderr)
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def get_session(grace_root: str | None = None):
    """Return a live SQLAlchemy session bound to the configured grace DB.

    This is the REAL `grace` onboarding database (not grace_test) — that is the
    point: we are populating the live CQ + review pipeline. Tests are the only
    thing that must run against grace_test (see safe_pytest.sh).
    """
    add_grace_to_path(grace_root)
    from src.shared.database import get_session_factory  # noqa: E402

    return get_session_factory()()


def distinct_domains(db) -> list[str]:
    """Distinct domains that have at least one COMPLETE processed document."""
    from src.discovery.database import ProcessedDocumentRow  # noqa: E402

    rows = (
        db.query(ProcessedDocumentRow.domain)
        .filter(ProcessedDocumentRow.status == "COMPLETE")
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows if r[0]})
