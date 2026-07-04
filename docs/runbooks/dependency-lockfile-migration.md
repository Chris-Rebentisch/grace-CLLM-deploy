# Dependency Lockfile Migration — pip to uv

Operator guide for migrating from per-package `pip install --break-system-packages` to `uv`-managed `pyproject.toml` + `uv.lock`.

**Decision:** D495 (uv adoption), D496 (three-group segmentation), D497 (migration runbook).

---

## 1. Pre-migration Backup

Snapshot your current pip environment before changing anything:

```bash
pip freeze --break-system-packages > ~/my-installs-backup-$(date +%Y%m%d).txt
```

Keep this file — it is your rollback source.

## 2. Bootstrap uv

```bash
pip install --break-system-packages uv
```

<!-- Capture-the-why per D356: this is the sole authorized post-chunk use of
     pip install --break-system-packages. Invariant: D495 (uv adoption) retires
     all other --break-system-packages usage; bootstrap-tool exception authorized
     by D495 §4. -->

Verify:

```bash
uv --version
```

## 3. Sync — Choose Mode A or Mode B

### Mode A — System sync (replaces system-installed packages in-place)

```bash
cd ~/grace
uv sync --extra dev
```

This creates a `.venv` at repo root and installs all runtime + dev dependencies into it. Activate it:

```bash
source .venv/bin/activate
```

### Mode B — Isolated side-by-side venv

```bash
cd ~/grace
uv venv .venv-uv
source .venv-uv/bin/activate
uv sync --extra dev
```

This leaves your system Python untouched.

## 4. Verify

After syncing, confirm key packages are importable:

```bash
python3 -c 'import pydantic, fastapi, alembic, pytest; print("OK")'
```

Run the test suite:

```bash
python3 -m pytest tests/ -q
```

Run the live-server smoke:

```bash
bash scripts/smoke-live-server.sh
```

## 5. Rollback

### Mode A rollback

If the uv-managed environment has issues, restore from your backup:

```bash
deactivate  # exit the .venv if active
rm -rf .venv
pip install --break-system-packages -r ~/my-installs-backup-YYYYMMDD.txt
```

### Mode B rollback

```bash
deactivate
rm -rf .venv-uv
```

Your system Python is unchanged — no further action needed.

## 6. Airgap Cache Distribution

For airgapped deployments, pre-warm the uv cache on a network-connected machine:

### On the connected machine

```bash
cd ~/grace
uv sync --extra dev          # populates ~/.cache/uv
tar czf uv-cache.tar.gz -C ~ .cache/uv
```

Transfer `uv-cache.tar.gz` to the airgapped machine.

### On the airgapped machine

```bash
tar xzf uv-cache.tar.gz -C ~
cd ~/grace
UV_OFFLINE=1 uv sync --extra dev
```

The `UV_OFFLINE=1` flag prevents any network requests. If sync fails, the cache is incomplete — re-warm on the connected machine with the full dependency set.

### Verify airgap operation

```bash
UV_OFFLINE=1 uv sync --extra dev && echo "Airgap OK"
```

## 7. Adding New Dependencies

After migration, all new Python packages are added via `uv`:

```bash
uv add <package-name>       # adds to [project.dependencies] + updates uv.lock
uv add --dev <package-name>  # adds to [project.optional-dependencies.dev]
```

Do NOT use `pip install --break-system-packages` for new packages. The only authorized use of that flag post-migration is bootstrapping `uv` itself (see §2).

## 8. Crawl4AI (Optional)

To install the optional crawl4ai integration (D227; disabled when `airgap_mode=true`):

```bash
uv sync --extra crawl
```
