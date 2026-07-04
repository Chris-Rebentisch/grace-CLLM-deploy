# First-Boot Walkthrough

Operator guide for bootstrapping a fresh GrACE development environment from a clean clone.

## Pre-requisites

- **Docker runtime:** Colima (macOS) or Docker Desktop
- **PostgreSQL 17:** local install or managed instance
- **Ollama:** installed and running on `localhost:11434`
- **Python 3.14:** with pip available
- **uv:** Python package manager (`pip install --break-system-packages uv` — bootstrap-tool exception per D495/D356)
- **Node.js 20+:** for the frontend (Next.js 15)

## Step-by-step

### 1. Clone and enter the repo

```bash
git clone <repo-url> grace && cd grace
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in values:

```bash
cp .env.example .env
```

Set `GRAFANA_POSTGRES_PASSWORD` to a secure value (not `CHANGEME`).

### 3. Install Python dependencies

Bootstrap `uv` (one-time) and sync all dependencies:

```bash
pip install --break-system-packages uv   # safe to re-run; installs only the uv bootstrap tool
uv sync --extra dev
source .venv/bin/activate
```

<!-- Capture-the-why per D356: pip install --break-system-packages is used here
     solely to bootstrap uv itself. All subsequent package installs use uv add.
     Authorization: D495 bootstrap-tool exception. -->

### 4. Start Docker runtime

- **macOS (Colima):**

  ```bash
  colima start --cpu 2 --memory 4 --disk 20
  ```

- **Windows:** launch **Docker Desktop** once so the engine is running (the Colima flags don't apply).
- **Linux:** native Docker — ensure the daemon is running (`systemctl start docker` or equivalent).

### 5. Start ArcadeDB

```bash
docker compose -f docker/docker-compose.arcade.yml up -d
```

Note: ArcadeDB binds `127.0.0.1` only (loopback per D455). For non-localhost topologies, create a `docker-compose.override.yml` with wider bindings.

### 6. Bootstrap the grace_readonly Postgres role

```bash
GRAFANA_POSTGRES_PASSWORD="$GRAFANA_POSTGRES_PASSWORD" bash scripts/setup/bootstrap_grace_readonly.sh
```

Verify with `--check` mode:

```bash
GRAFANA_POSTGRES_PASSWORD="$GRAFANA_POSTGRES_PASSWORD" bash scripts/setup/bootstrap_grace_readonly.sh --check
```

### 7. Apply database migrations

```bash
alembic upgrade head
```

### 8. Start the observability stack

```bash
docker compose -f docker/docker-compose.observability.yml up -d
```

### 9. Run the Grafana preflight check

```bash
bash scripts/preflight/grafana_health_check.sh
```

If the check fails, it emits a JSON diagnostic identifying the defect class and recommended fix.

### 10. Start the API server

```bash
uvicorn src.api.main:app --reload --port 8000
```

### 11. Start the frontend (optional)

```bash
cd frontend && npm install && npm run dev
```

## Verification

After completing the steps above, verify each service is reachable:

| Service | URL | Expected |
|---|---|---|
| FastAPI (Swagger) | `http://localhost:8000/docs` | Swagger UI |
| Frontend | `http://localhost:3000` | Next.js app |
| ArcadeDB Studio | `http://localhost:2480` | Login page (root/gracedev) |
| Prometheus | `http://localhost:9090` | Prometheus UI |
| Grafana | `http://localhost:3001` | Login page (admin/gracedev) |
| Ollama | `http://localhost:11434` | Version response |

## Non-default scenarios

### Non-loopback ArcadeDB binding

If ArcadeDB must be accessible from other hosts, create `docker/docker-compose.override.yml`:

```yaml
services:
  arcadedb:
    ports:
      - "0.0.0.0:2480:2480"
      - "0.0.0.0:2424:2424"
```

Then restart: `docker compose -f docker/docker-compose.arcade.yml up -d`.

### Managed PostgreSQL

For non-localhost Postgres, set connection env vars before running the bootstrap script:

```bash
export PGUSER=admin
export PGPASSWORD=secret
export PGHOST=db.example.com
export PGPORT=5432
export PGDATABASE=grace
GRAFANA_POSTGRES_PASSWORD="$GRAFANA_POSTGRES_PASSWORD" bash scripts/setup/bootstrap_grace_readonly.sh
```
