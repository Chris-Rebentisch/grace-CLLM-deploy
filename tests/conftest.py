"""Top-level pytest fixtures shared across the GrACE test suite.

The ``permissive_permission_matrix`` autouse fixture seeds a default-allow
``PermissionMatrix`` into the process-wide enforcer so that test clients
exercising mutating routes do not trip ``PermissionMatrixMiddleware``'s
``no_active_matrix`` deny path. Production callers ratify a real matrix
via ``POST /api/permissions/matrix/ratify`` — that path is exercised
directly in ``tests/permissions/`` and ``tests/api/test_permissions_routes.py``,
which manage their own matrix state via ``rebuild_enforcer``.
"""

from __future__ import annotations

import fcntl
import os
import sys
import urllib.parse

import pytest

# D487 — GRACE_PYTEST_MODE env-var bootstrap (Chunk 75a).
# setdefault so operators can override with GRACE_PYTEST_MODE=0 to exercise
# the production OTel path under pytest. Authorization: D487 / spec §6 Step 1.
os.environ.setdefault("GRACE_PYTEST_MODE", "1")


# ---------------------------------------------------------------------------
# Test-database isolation (three-tier test setup, 2026-05-28)
#
# Why: pytest fixtures here mutate (and historically TRUNCATE/commit) DB state.
# The operator's ``grace`` dev database also holds the GOLD corpus
# (extraction_claims, processed_documents, ...). A stray ``pytest tests/``
# against ``grace`` wiped extraction_claims 3,194 -> 0 (the R3-H1 incident
# class). Rather than police every fixture, we ISOLATE: the test process always
# targets the ``_test`` sibling database (e.g. grace -> grace_test), which
# pytest may freely wipe. The GOLD corpus in ``grace`` is then physically
# unreachable from tests.
#
# Must run BEFORE any ``src.*`` import below: src.shared.database builds its
# engine lazily from GraceSettings, which reads DATABASE_URL from the
# environment (env var takes precedence over .env). Setting os.environ here
# guarantees the engine binds to the test DB.
#
# Override precedence:
#   1. GRACE_PYTEST_DATABASE_URL          -> used verbatim (operator opt-in)
#   2. DATABASE_URL already ending _test  -> left as-is
#   3. otherwise                          -> the ``_test`` sibling of DATABASE_URL
# ---------------------------------------------------------------------------
def _isolate_test_database_url() -> None:
    explicit = os.environ.get("GRACE_PYTEST_DATABASE_URL")
    if explicit:
        os.environ["DATABASE_URL"] = explicit
        return
    base = os.environ.get("DATABASE_URL", "")
    if not base:
        # DATABASE_URL often lives only in .env (loaded later by GraceSettings).
        # Read it directly so we can derive the test sibling now.
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        try:
            with open(env_path) as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped.startswith("DATABASE_URL=") and not stripped.startswith("#"):
                        base = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except OSError:
            pass
    if not base:
        return  # nothing to isolate; the D472 guard below still applies
    parsed = urllib.parse.urlparse(base)
    db_name = (parsed.path or "").lstrip("/")
    if db_name.endswith("_test"):
        os.environ["DATABASE_URL"] = base
        return
    test_url = urllib.parse.urlunparse(parsed._replace(path="/" + db_name + "_test"))
    os.environ["DATABASE_URL"] = test_url


_isolate_test_database_url()


def _isolate_test_arcade_database() -> None:
    """Mirror the Postgres isolation for ArcadeDB (F-022 companion).

    ``ArcadeConfig.database`` now honors ``ARCADE_DATABASE`` (F-022), and
    ``get_arcade_client()`` reads the same setting — an env var set here wins
    over ``.env`` for both paths. Without this, the ArcadeDB target of a
    ``pytest tests/`` run silently depends on the operator's ``.env``; a
    default install would point service-dependent tests at the LIVE graph.
    Same precedence as ``_isolate_test_database_url``: an explicit ``_test``
    value (env or .env) is respected, anything else is redirected to its
    ``_test`` sibling.
    """
    current = os.environ.get("ARCADE_DATABASE", "")
    if not current:
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        try:
            with open(env_path) as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped.startswith("ARCADE_DATABASE=") and not stripped.startswith("#"):
                        current = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except OSError:
            pass
    if current.endswith("_test"):
        os.environ["ARCADE_DATABASE"] = current
        return
    os.environ["ARCADE_DATABASE"] = (current or "grace") + "_test"


_isolate_test_arcade_database()

from src.permissions.enforcer import rebuild_enforcer
from src.permissions.models import PermissionMatrix


# ---------------------------------------------------------------------------
# D472 — Pytest DATABASE_URL wipe guard
#
# Blocks test collection when DATABASE_URL points at a non-test database.
# Three recognized opt-in patterns (any one sufficient):
#   1. URL path ends in ``_test`` suffix.
#   2. URL hostname is localhost/127.0.0.1 AND ``GRACE_TEST_DB=1`` is set.
#   3. URL exactly matches ``GRACE_PYTEST_DATABASE_URL`` env var.
#
# Rejected: URL substrings or hostname containing ``prod``, ``production``,
# ``gold``, ``live``.
#
# When DATABASE_URL is not set the guard passes silently — some tests
# don't need a DB.
# ---------------------------------------------------------------------------

_REJECTED_SUBSTRINGS = ("prod", "production", "gold", "live")


def _is_test_database_url(url: str) -> bool:
    """Return True when *url* is unambiguously a test database URL.

    D472: Three opt-in patterns, plus a reject list.
    """
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower()

    # Hard-reject dangerous hostnames/paths regardless of opt-in.
    url_lower = url.lower()
    for word in _REJECTED_SUBSTRINGS:
        if word in hostname or word in (parsed.path or "").lower():
            return False

    # Opt-in 1: URL path ends in ``_test`` suffix.
    db_name = (parsed.path or "").rstrip("/")
    if db_name.endswith("_test"):
        return True

    # Opt-in 2: localhost + GRACE_TEST_DB=1.
    if hostname in ("localhost", "127.0.0.1") and os.environ.get("GRACE_TEST_DB") == "1":
        return True

    # Opt-in 3: explicit match via GRACE_PYTEST_DATABASE_URL.
    explicit = os.environ.get("GRACE_PYTEST_DATABASE_URL", "")
    if explicit and url == explicit:
        return True

    return False


def pytest_configure(config):  # noqa: ARG001 — pytest hook signature
    """D472: session-start guard — block test collection against non-test databases.

    Invokes ``pytest.exit(msg, returncode=78)`` when ``DATABASE_URL`` is set
    but does not satisfy any of the three opt-in patterns.

    Also engages the full-suite single-flight guard (returncode 86) for broad
    ``pytest tests/`` runs to prevent the concurrent-run Postgres deadlock.
    """
    _engage_fullsuite_single_flight(config)

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        # No DATABASE_URL set — some tests don't need a DB. Pass silently.
        return

    if _is_test_database_url(db_url):
        return

    msg = (
        "\n\n"
        "=== PYTEST DB-WIPE GUARD ===\n\n"
        f"DATABASE_URL is set to a non-test database:\n  {db_url}\n\n"
        "Test collection has been blocked to prevent accidental data loss.\n\n"
        "Three opt-in patterns (any one sufficient):\n"
        "  1. URL path ends in '_test' suffix (e.g. postgresql://localhost/grace_test)\n"
        "  2. URL hostname is localhost/127.0.0.1 AND env var GRACE_TEST_DB=1 is set\n"
        "  3. URL exactly matches GRACE_PYTEST_DATABASE_URL env var\n\n"
        "See docs/runbooks/pytest-db-safety.md for details.\n"
    )
    pytest.exit(msg, returncode=78)


# ---------------------------------------------------------------------------
# Full-suite single-flight guard (chunks 73–76 build campaign, 2026-05-27)
#
# Why: during the chunk-74 audit stage, the audit-author launched
# ``pytest tests/`` TWICE concurrently. Two full-repository runs contend on
# the same Postgres ``grace`` database (each holds 10+ connections / open
# transactions) and DEADLOCK — both hang 25-30 min with zero progress,
# stalling the whole pipeline. A single run progresses fine; only the
# concurrency is fatal. See:
#   - docs/build-automation-chunks-65-81-reference.md §6 (proposed fixes)
#   - chunk-74 audit DV1 / R4
#
# Carve-out / scope: this guard engages ONLY for *broad* runs that collect
# the whole ``tests/`` tree (the deadlock-prone shape). Scoped runs that name
# specific files / modules / node-ids are left untouched, so parallel
# targeted test runs (and the chunk-scoped audit pattern) keep working.
#
# Opt-out: ``GRACE_ALLOW_CONCURRENT_FULLSUITE=1`` disables the guard entirely.
#
# NOTE TO CHUNK 75a (test-stability triage, which reworks conftest for
# SAVEPOINT fixtures + GRACE_PYTEST_MODE + collection hooks): PRESERVE this
# guard or fold it into the new infra. It is a deadlock-prevention safety
# net, orthogonal to the SAVEPOINT/rollback work.
# ---------------------------------------------------------------------------

_FULLSUITE_LOCK_PATH = "/tmp/grace-pytest-fullsuite.lock"
_fullsuite_lock_fd = None  # module global keeps the fd (and the lock) alive


def _is_broad_fullsuite_run(config) -> bool:
    """Return True when the invocation collects the whole ``tests/`` tree.

    Broad shapes (deadlock-prone): no positional args (rootdir collection),
    or every positional arg is the bare ``tests`` / ``tests/`` directory or
    the repo root (``.``). Any arg naming a specific file, module path, or
    ``::`` node-id is a *scoped* run and is exempt.
    """
    args = [str(a) for a in getattr(config, "args", []) or []]
    if not args:
        return True
    broad_targets = {"tests", "tests/", ".", "./"}
    for a in args:
        norm = a.split("::", 1)[0].rstrip("/")
        norm_slash = norm + "/"
        if a in broad_targets or norm in {"tests", "."} or norm_slash in broad_targets:
            continue
        # A specific file or subdirectory was named → scoped run.
        return False
    return True


def _engage_fullsuite_single_flight(config) -> None:
    """Acquire a non-blocking flock for broad full-suite runs.

    If another broad run already holds the lock, exit immediately
    (returncode 86) rather than deadlocking on the shared Postgres DB.
    """
    global _fullsuite_lock_fd

    if os.environ.get("GRACE_ALLOW_CONCURRENT_FULLSUITE") == "1":
        return
    if not _is_broad_fullsuite_run(config):
        return

    try:
        fd = open(_FULLSUITE_LOCK_PATH, "w")
    except OSError:
        # Cannot create the lockfile (read-only /tmp etc.) — fail open.
        return

    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        msg = (
            "\n\n"
            "=== PYTEST FULL-SUITE SINGLE-FLIGHT GUARD ===\n\n"
            "Another broad `pytest tests/` run is already in progress "
            f"(lock: {_FULLSUITE_LOCK_PATH}).\n\n"
            "Two concurrent full-repository runs deadlock on the shared "
            "Postgres database. This run was refused to prevent the deadlock.\n\n"
            "Options:\n"
            "  - Wait for the in-flight run to finish, then retry.\n"
            "  - Run a SCOPED subset instead (name specific files/modules), "
            "which is exempt from this guard.\n"
            "  - Set GRACE_ALLOW_CONCURRENT_FULLSUITE=1 to override (not "
            "recommended — risks the deadlock).\n"
        )
        print(msg, file=sys.stderr)
        pytest.exit(msg, returncode=86)

    # Hold the fd open for the process lifetime; lock releases on exit.
    _fullsuite_lock_fd = fd


# ---------------------------------------------------------------------------
# D486 — Test-suite allowlist (Chunk 75a)
#
# Why: the chunk-72b PASS_WITH_DEVIATIONS root cause showed that residual test
# failures without per-entry ownership lead to stalled pipelines.  The
# allowlist (docs/test-suite-allowlist.md) is the centralized skip registry
# for ≤5 known failures — each entry must have owner + fix_by metadata.
# This hook is the SOLE collection-time skip path for the centralized
# allowlist.  Authorization: D486 / spec §6 Step 3.
# ---------------------------------------------------------------------------

_ALLOWLIST_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "docs", "test-suite-allowlist.md"
)
_VALID_FAILURE_CLASSES = frozenset({"graph-state", "co-tenant", "flaky", "environmental"})
_ALLOWLIST_MAX_ROWS = 5


def _parse_allowlist(path: str) -> list[dict[str, str]]:
    """Parse the Markdown table in *path* and return data rows.

    Each row is a dict with keys: test_id, failure_class, owner, fix_by_chunk, rationale.
    Raises ValueError on schema violations.
    """
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []

    rows: list[dict[str, str]] = []
    header_seen = False
    separator_seen = False

    for line in lines:
        stripped = line.strip()
        if not stripped or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 5:
            continue

        # First pipe-delimited line is header
        if not header_seen:
            header_seen = True
            continue
        # Second is separator (---|---|...)
        if not separator_seen:
            separator_seen = True
            continue

        # Data row
        test_id, failure_class, owner, fix_by_chunk, rationale = (
            cells[0], cells[1], cells[2], cells[3], cells[4]
        )
        if not test_id:
            continue  # skip empty rows

        if not owner:
            raise ValueError(
                f"Allowlist entry for '{test_id}' has empty owner. "
                "Every allowlisted failure must have an assigned owner."
            )
        if failure_class not in _VALID_FAILURE_CLASSES:
            raise ValueError(
                f"Allowlist entry for '{test_id}' has invalid failure_class "
                f"'{failure_class}'. Must be one of: {sorted(_VALID_FAILURE_CLASSES)}"
            )
        rows.append({
            "test_id": test_id,
            "failure_class": failure_class,
            "owner": owner,
            "fix_by_chunk": fix_by_chunk,
            "rationale": rationale,
        })

    if len(rows) > _ALLOWLIST_MAX_ROWS:
        raise ValueError(
            f"Allowlist has {len(rows)} entries but maximum is {_ALLOWLIST_MAX_ROWS}. "
            "Fix or remove entries before adding new ones."
        )
    return rows


# ---------------------------------------------------------------------------
# Service / corpus marker auto-skip (three-tier test setup, 2026-05-28)
#
# Tier 1 (always-on net): fast tests with no external dependency — run anywhere.
# Tier 2 (isolated integration): tests that need a live service or seeded data,
#   marked with one of the requires_* markers below. They auto-skip when the
#   dependency is absent so a bare-box `pytest tests/` stays green, instead of
#   producing environment-driven failures.
#
# GRACE_REQUIRE_SERVICES=1 forces marked tests to RUN (and fail loudly if the
# dependency is missing) — for a CI tier that provisions the services/corpus.
#
# - requires_ollama / requires_arcade / requires_live_server: probed live
#   (reachability), result cached once per session.
# - requires_graph_corpus: opt-in only. There is no reliable probe for "the
#   graph is seeded with the right fixtures", so these skip unless forced.
# ---------------------------------------------------------------------------

def _probe_ollama() -> bool:
    import httpx
    try:
        return httpx.get("http://localhost:11434/v1/models", timeout=1.5).status_code == 200
    except Exception:
        return False


def _probe_arcade() -> bool:
    import httpx
    try:
        # Any HTTP response (even 401) means ArcadeDB is reachable.
        httpx.get("http://localhost:2480/", timeout=1.5)
        return True
    except Exception:
        return False


def _probe_live_server() -> bool:
    import httpx
    try:
        return httpx.get("http://localhost:8000/api/graph/info", timeout=1.5).status_code == 200
    except Exception:
        return False


def _probe_ocr() -> bool:
    """Check if the platform-appropriate OCR backend is importable."""
    import sys as _sys
    try:
        if _sys.platform == "darwin":
            import ocrmac  # noqa: F401
        else:
            import onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def _probe_vision() -> bool:
    """Check if a vision-capable LLM is reachable (Ollama qwen2.5-vl)."""
    try:
        import httpx
        resp = httpx.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code != 200:
            return False
        models = resp.json().get("models", [])
        return any("vl" in m.get("name", "").lower() for m in models)
    except Exception:
        return False


def _probe_nltk() -> bool:
    """Check if NLTK POS tagger data is available (Chunk 78)."""
    try:
        import nltk
        nltk.pos_tag(["test"])
        return True
    except Exception:
        return False


_SERVICE_PROBES = {
    "requires_ollama": _probe_ollama,
    "requires_arcade": _probe_arcade,
    "requires_live_server": _probe_live_server,
    "requires_ocr": _probe_ocr,
    "requires_vision": _probe_vision,
    "requires_nltk": _probe_nltk,
}


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """D486 allowlist skips + three-tier service/corpus marker auto-skips."""
    # --- D486 centralized allowlist ---
    try:
        rows = _parse_allowlist(_ALLOWLIST_PATH)
    except ValueError as exc:
        # Surface the error clearly rather than silently passing.
        print(f"\n=== ALLOWLIST PARSE ERROR ===\n{exc}\n", file=sys.stderr)
        rows = []
    allowlist_map = {r["test_id"]: r for r in rows}

    # --- service / corpus marker auto-skip ---
    force_services = os.environ.get("GRACE_REQUIRE_SERVICES") == "1"
    probe_cache: dict[str, bool] = {}

    def _available(marker: str) -> bool:
        if marker not in probe_cache:
            probe_cache[marker] = _SERVICE_PROBES[marker]()
        return probe_cache[marker]

    for item in items:
        # 1. Centralized allowlist (highest precedence).
        if item.nodeid in allowlist_map:
            entry = allowlist_map[item.nodeid]
            item.add_marker(pytest.mark.skip(
                reason=f"allowlisted: owner={entry['owner']}, fix_by={entry['fix_by_chunk']}"
            ))
            continue

        if force_services:
            continue  # CI tier provisions services/corpus — run everything, fail loud.

        # 2. requires_graph_corpus — opt-in only (no reliable "is-seeded" probe).
        if item.get_closest_marker("requires_graph_corpus"):
            item.add_marker(pytest.mark.skip(
                reason="requires_graph_corpus: needs a seeded ArcadeDB graph corpus "
                       "(set GRACE_REQUIRE_SERVICES=1 to run)"
            ))
            continue

        # 3. Probed service markers.
        for marker in _SERVICE_PROBES:
            if item.get_closest_marker(marker) and not _available(marker):
                item.add_marker(pytest.mark.skip(reason=f"{marker}: service unavailable"))
                break


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def permissive_permission_matrix() -> None:
    rebuild_enforcer(PermissionMatrix(default_decision="allow"))
    yield
    rebuild_enforcer(None)


@pytest.fixture(autouse=True)
def _redirect_repo_config_files(tmp_path_factory, monkeypatch):
    """Repo-config isolation (ISS-0001): pytest must never mutate the real
    ``config/discovery.yaml`` or ``.env``.

    Any test that reaches ``write_llm_config_to_yaml`` / ``update_env_api_key``
    — directly or through ``POST /api/llm/config`` on the real app (the
    support-session pass-through test did exactly that) — previously rewrote
    the repo config with PyYAML output: comments stripped, provider flipped to
    whatever the test posted. Redirect the module-level path globals to
    per-test tmp copies so reads see real content and writes land in tmp.

    Uses ``tmp_path_factory`` (a sibling dir), NOT the test's own ``tmp_path``
    — tests that scan their ``tmp_path`` (e.g. decomposition layer-1 walk)
    must not see these copies.
    """
    import shutil

    from src.shared import llm_provider as lp

    iso_dir = tmp_path_factory.mktemp("repo-config-isolation")
    yaml_copy = iso_dir / "discovery.yaml"
    if lp._DISCOVERY_YAML.exists():
        shutil.copyfile(lp._DISCOVERY_YAML, yaml_copy)
    env_copy = iso_dir / ".env"
    if lp._ENV_PATH.exists():
        shutil.copyfile(lp._ENV_PATH, env_copy)
    monkeypatch.setattr(lp, "_DISCOVERY_YAML", yaml_copy)
    monkeypatch.setattr(lp, "_ENV_PATH", env_copy)
