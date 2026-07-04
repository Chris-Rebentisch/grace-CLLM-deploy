---
name: grace-testing-protocol
description: >
  The GrACE testing + safety doctrine distilled from the build sessions. Three-tier
  test model, grace_test DB isolation (never touch the GOLD corpus), service markers,
  and the heat / Ollama operational rules. Invoke before running ANY pytest or any
  command that could load gpt-oss. Read this when you are about to "just run the tests".
---

# grace-testing-protocol

## The one rule that matters most
**Tests must never touch the live `grace` GOLD corpus.** A prior `pytest tests/` once
drained `extraction_claims` 3194 → 0. Two layers now prevent that — do not disable
either.

## Test-DB isolation (primary layer)
`tests/conftest.py` redirects the test process to the `grace_test` sibling DB at import
time, before the SQLAlchemy engine is built. Destructive fixtures therefore physically
cannot reach `grace`. Redirect precedence:
1. `GRACE_PYTEST_DATABASE_URL` (verbatim, highest)
2. a `DATABASE_URL` already ending `_test`
3. the derived `_test` sibling of the configured DB (`grace` → `grace_test`)

**One-time setup:**
```bash
createdb grace_test
DATABASE_URL=postgresql+psycopg2://$USER@localhost:5432/grace_test alembic upgrade head
# re-run the upgrade against grace_test whenever new migrations land
```

## DB-wipe guard (backstop layer)
Beneath isolation, `pytest_configure()` calls `pytest.exit(78)` if `DATABASE_URL` still
doesn't match a test-safe pattern. Rejected substrings: `prod`, `production`, `gold`,
`live`. Never weaken it.

## Three-tier model
- **Tier 1 — unit/contract:** no DB, no services. Always runnable.
- **Tier 2 — isolated integration:** runs against `grace_test`.
- **Tier 3 — manual end-to-end:** real `grace`, MCP/API, run deliberately by hand.

Service-dependent tests carry markers `requires_ollama` / `requires_arcade` /
`requires_live_server` / `requires_graph_corpus` and **auto-skip** when the dependency
is absent. Force them with `GRACE_REQUIRE_SERVICES=1`. The D219 live smoke harness is
`@pytest.mark.smoke` and excluded from the default run.

## How to run (use the wrapper)
```bash
~/grace-claude-skills/scripts/safe_pytest.sh                    # full safe suite
~/grace-claude-skills/scripts/safe_pytest.sh tests/discovery -v # scoped
```
The wrapper refuses prod/gold/live DATABASE_URLs and uses the repo `.venv`.

## Triage, don't blanket-skip
When a test fails, triage the cause. Known-failures live in
`docs/test-suite-allowlist.md` (≤5 entries, closed failure-class enum). Full-repo
`pytest tests/` may exit non-zero ONLY from those documented entries.

## Heat / Ollama operational rules (hard-won)
- **Keep gpt-oss:120b UNLOADED unless explicitly authorized.** It is ~65 GB and
  overheats the host. `ollama stop` / `keep_alive: 0` to unload.
- **Killing uvicorn does NOT stop Ollama.** Ollama keeps grinding queued requests to
  completion or the 600 s timeout. To actually stop heat you must BOTH unload the model
  AND clear the queue: hard-kill `llama-server`, then quit the Ollama app
  (`osascript -e 'quit app "Ollama"'` + `pkill`).
- **Single inference slot = serial.** Queued gpt-oss requests run one at a time;
  spawning more does not parallelize, it just lengthens the queue.
- This whole skill bundle exists to avoid loading gpt-oss at all — Claude is the LLM.

## Python interpreter
System `python3` may be 3.9 and fail imports. Always use the repo venv:
`~/grace/.venv/bin/python`. For async helpers like `embed_texts(...)`, it needs a
`base_url` and must be awaited (`asyncio.run(embed_texts(texts, "http://localhost:11434"))`).
