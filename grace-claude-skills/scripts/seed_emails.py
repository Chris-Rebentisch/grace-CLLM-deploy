"""grace-ingestion-harness — SANDBOX EMAIL SEEDER (C1).

Seeds a KNOWN organization email substrate + a sender registry into the
`grace_test` sandbox so the deterministic ingestion pipeline can be scored
against the corpus manifest. The .eml corpus is the ground truth; this seeder
makes the real adapter parse it (no shortcut INSERT) and stands up the
`Person`/`Organization` registry that triage Tier 2 requires (D274/D430).

SAFETY (load-bearing): refuses any database whose name does not end in `_test`.
The ArcadeDB sandbox graph is a SEPARATE db (`grace_test`), never GOLD `grace`.

What it does:
  1. (--reset/--clean) TRUNCATE the sandbox email tables (TRUNCATE bypasses the
     append-only DELETE guard) and drop the registry vertices.
  2. Ensure the `grace_test` ArcadeDB graph + `Person`/`Organization` vertex
     types + registry vertices for the corpus's known senders.
  3. INSERT an `eml` ingestion_sources row pointing at the corpus dir.
  4. Run the REAL IngestionPipeline pull (post-D536/D537) to populate
     communication_events from the .eml files.

Usage:
  python3 seed_emails.py                 # reset + seed registry + pull corpus
  python3 seed_emails.py --clean         # reset only (truncate + drop registry)
  python3 seed_emails.py --db-url <url>  # sandbox override (must end _test)
  python3 seed_emails.py --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402

HARNESS_ROOT = Path(__file__).resolve().parent.parent / "grace-ingestion-harness"
CORPUS_DIR = HARNESS_ROOT / "corpus"

# Known senders → registry vertices (Tier 2 matches sender_display_name).
REGISTRY_PERSONS = [
    {"name": "Alice Acme", "aliases": ["alice@acme-fo.example"]},
    {"name": "Robert Mason", "aliases": ["robert@birch-advisors.example"]},
]
REGISTRY_ORGS = [
    {"name": "Birch Advisors", "aliases": ["birch-advisors.example"]},
]
_MARKER = "c1probe"  # segment marker for the seeded source


def _sandbox_url(db_url: str | None) -> str:
    import os

    raw = db_url or os.environ.get("GRACE_PYTEST_DATABASE_URL") or os.environ.get(
        "DATABASE_URL"
    ) or "postgresql+psycopg2://localhost:5432/grace"
    head, _, dbname = raw.rpartition("/")
    if not dbname.endswith("_test"):
        dbname = f"{dbname}_test"
    url = f"{head}/{dbname}"
    if not url.rpartition("/")[2].endswith("_test"):
        raise SystemExit(f"REFUSE: sandbox-only, db name must end in '_test' (got {url})")
    return url


def _arcade_db_from(url: str) -> str:
    return url.rpartition("/")[2]  # e.g. grace_test


async def _seed_registry(arcade_db: str, drop: bool) -> dict:
    from src.graph.arcade_client import ArcadeClient
    from src.graph.config import ArcadeConfig

    c = ArcadeClient(ArcadeConfig(database=arcade_db))
    await c.ensure_database(arcade_db)
    out = {"persons": 0, "orgs": 0, "dropped": 0}
    for label in ("Person", "Organization"):
        try:
            await c.execute_sql(f"CREATE VERTEX TYPE {label} IF NOT EXISTS")
        except Exception:
            pass
    if drop:
        for label in ("Person", "Organization"):
            try:
                r = await c.execute_cypher(
                    f"MATCH (n:{label}) WHERE n.seeded_by = 'c1probe' DETACH DELETE n"
                )
                out["dropped"] += 1
            except Exception:
                pass
        await c.aclose()
        return out
    for p in REGISTRY_PERSONS:
        gid = str(uuid.uuid4())
        aliases = json.dumps(p["aliases"])
        await c.execute_cypher(
            "CREATE (n:Person {grace_id:$gid, name:$name, aliases:" + aliases
            + ", seeded_by:'c1probe'})",
            params={"gid": gid, "name": p["name"]},
        )
        out["persons"] += 1
    for o in REGISTRY_ORGS:
        gid = str(uuid.uuid4())
        aliases = json.dumps(o["aliases"])
        await c.execute_cypher(
            "CREATE (n:Organization {grace_id:$gid, name:$name, aliases:" + aliases
            + ", seeded_by:'c1probe'})",
            params={"gid": gid, "name": o["name"]},
        )
        out["orgs"] += 1
    await c.aclose()
    return out


def _reset_pg(engine) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE communication_events, communication_sensitivity_propagation, "
                "ingestion_runs, ingestion_sources CASCADE"
            )
        )


def _seed_source_and_pull(engine, arcade_db: str) -> dict:
    from sqlalchemy import text
    import src.ingestion.adapters  # noqa: F401  (registry; also done by pipeline post-D536)
    from src.shared.database import get_session_factory
    from src.ingestion.pipeline import IngestionPipeline

    src_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ingestion_sources (id,name,source_type,config_json,segment,"
                "enabled,created_at,status) VALUES (:id,'c1_corpus','eml',"
                "CAST(:cfg AS jsonb),:seg,true,now(),'ready')"
            ),
            {
                "id": str(src_id),
                "cfg": json.dumps(
                    {"source_type": "eml", "directory_path": str(CORPUS_DIR)}
                ),
                "seg": _MARKER,
            },
        )
    db = get_session_factory()()
    try:
        run_id = asyncio.run(IngestionPipeline(db).run(src_id))
    finally:
        db.close()
    with engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM communication_events")).scalar()
    return {"source_id": str(src_id), "run_id": str(run_id), "events": int(n)}


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--clean", action="store_true", help="reset sandbox + drop registry, exit")
    ap.add_argument("--db-url", default=None, help="sandbox URL override (must end _test)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true", default=True)
    args = ap.parse_args()

    route_logs_to_stderr(quiet=args.quiet)
    add_grace_to_path()
    import os
    url = _sandbox_url(args.db_url)
    os.environ["DATABASE_URL"] = url
    arcade_db = _arcade_db_from(url)
    os.environ["ARCADE_DATABASE"] = arcade_db

    from sqlalchemy import create_engine

    engine = create_engine(url)
    result = {"sandbox": url, "arcade_db": arcade_db}

    # Always reset first (idempotent, repeatable gate).
    _reset_pg(engine)
    # Registry seeding touches ArcadeDB; tolerate its absence (the Postgres green
    # gates — parse/T1/sensitivity/thread — do not depend on the registry, which only
    # matters for the deferred T2-pass path blocked by findings #3/#7).
    try:
        result["registry_dropped"] = asyncio.run(_seed_registry(arcade_db, drop=True))
    except Exception as e:
        result["registry_dropped"] = f"skipped ({type(e).__name__})"
    if args.clean:
        result["cleaned"] = True
    else:
        try:
            result["registry"] = asyncio.run(_seed_registry(arcade_db, drop=False))
        except Exception as e:
            result["registry"] = f"skipped ({type(e).__name__})"
        result.update(_seed_source_and_pull(engine, arcade_db))

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for k, v in result.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
