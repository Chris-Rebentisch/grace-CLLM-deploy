# Pytest DATABASE_URL Safety Guard (D472)

## Overview

GrACE's test suite includes a session-start guard in `tests/conftest.py` that blocks `pytest` from running when `DATABASE_URL` points to a non-test database. This prevents accidental data loss (e.g., TRUNCATE of production tables by test fixtures).

## Automatic Test-DB Isolation (primary layer, 2026-05-28)

Above the D472 guard sits a stronger, automatic layer: **the test process never targets your `grace` dev database — it is redirected to the `grace_test` sibling.**

`tests/conftest.py` runs `_isolate_test_database_url()` at import time (before any `src.*` import builds the SQLAlchemy engine). It rewrites `DATABASE_URL` to the `_test` sibling of whatever is configured (`grace` → `grace_test`), so destructive fixtures (TRUNCATE / DELETE+commit) only ever hit `grace_test`. Your GOLD corpus in `grace` is physically unreachable from tests.

Override precedence:
1. `GRACE_PYTEST_DATABASE_URL` — used verbatim (explicit operator opt-in).
2. `DATABASE_URL` already ending in `_test` — left as-is.
3. otherwise — the `_test` sibling of `DATABASE_URL` is forced.

Because the redirected URL ends in `_test`, the D472 guard (below) is satisfied automatically — you no longer need `GRACE_TEST_DB=1` for local runs.

**One-time setup of the test DB:**
```bash
createdb grace_test
DATABASE_URL=postgresql+psycopg2://<user>@localhost:5432/grace_test alembic upgrade head
```
Re-run `alembic upgrade head` against `grace_test` whenever new migrations land.

If the `grace_readonly` role exists on your Postgres cluster (created by
`scripts/setup/bootstrap_grace_readonly.sh` for the main `grace` DB), also grant it
read access inside `grace_test` — otherwise the migration-grant tests fail (they
skip cleanly only when the role is absent entirely):
```bash
psql grace_test -c "GRANT USAGE ON SCHEMA public TO grace_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grace_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grace_readonly;"
```

This is the foundation of the three-tier test model: Tier 1 fast unit/contract tests (no DB), Tier 2 isolated integration tests against `grace_test`, Tier 3 manual MCP/API end-to-end against the real `grace`.

## How It Works

The `pytest_configure()` hook reads `DATABASE_URL` from the environment at session start. If the URL is set but does not match any of the three opt-in patterns, `pytest.exit(msg, returncode=78)` fires before test collection begins.

## Three Opt-In Patterns

Any **one** of the following is sufficient to pass the guard:

### 1. `_test` Suffix

The database name (URL path component) ends in `_test`.

```
DATABASE_URL=postgresql://localhost/grace_test   # passes
DATABASE_URL=postgresql://localhost/grace         # blocked
```

### 2. Localhost + `GRACE_TEST_DB=1`

The URL hostname is `localhost` or `127.0.0.1` **and** the environment variable `GRACE_TEST_DB` is set to `1`.

```bash
export DATABASE_URL=postgresql://localhost/grace
export GRACE_TEST_DB=1
pytest tests/  # passes
```

### 3. Explicit `GRACE_PYTEST_DATABASE_URL` Match

The `DATABASE_URL` value exactly matches the `GRACE_PYTEST_DATABASE_URL` environment variable. Use this for CI environments with non-standard naming.

```bash
export DATABASE_URL=postgresql://ci-host:5432/ci_db_1234
export GRACE_PYTEST_DATABASE_URL=postgresql://ci-host:5432/ci_db_1234
pytest tests/  # passes
```

## Rejected Patterns

URLs containing any of these substrings (in hostname or path) are **always** rejected, even if an opt-in pattern would otherwise match:

- `prod`
- `production`
- `gold`
- `live`

## When `DATABASE_URL` Is Unset

If `DATABASE_URL` is not set at all, the guard passes silently. Some tests don't need a database connection.

## Migration Guidance for CI

1. **Recommended:** Use a `_test`-suffixed database name (e.g., `grace_test`). No extra env vars needed.
2. **Alternative:** Set `GRACE_TEST_DB=1` alongside `DATABASE_URL` in CI config.
3. **Escape hatch:** Set `GRACE_PYTEST_DATABASE_URL` to the exact CI database URL.

## Troubleshooting

If you see the `PYTEST DB-WIPE GUARD` message:

1. Check your `DATABASE_URL` — does it point to a test database?
2. If yes, ensure the database name ends in `_test` or set `GRACE_TEST_DB=1`.
3. If using a custom CI database, set `GRACE_PYTEST_DATABASE_URL` to the exact URL.
4. **Never** disable this guard. The cost of a false positive (one re-run with an env var) is far lower than the cost of a false negative (production data loss).

## Cross-References

- `tests/conftest.py` — implementation
- `tests/scripts/test_pytest_db_wipe_guard.py` — regression tests
- `docs/security-posture.md` §56 — security posture entry
