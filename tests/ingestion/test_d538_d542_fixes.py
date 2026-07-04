"""Guard tests for the C1 deferred-finding fixes (D538-D542), surfaced by the
grace-ingestion-harness live proof.

D538 — triage Tier 2 + role_resolver route arcade access through get_arcade_client()
       (honors ARCADE_DATABASE) instead of bare ArcadeClient() (hardcoded "grace").
D539 — EmlAdapter captures raw_headers; calendar T1 detector also reads Content-Type.
D540 — Tier 2 entity-match labels are configurable (Tier2Config.entity_types).
D541 — supersession write awaits the cypher (no fire-and-forget) and resets the pool
       so it never raises "Event loop is closed".
D542 — corroboration run flow promotes multi-source facts and (echo-dedup) does NOT
       promote single-source or same-sender-echo facts.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# D538 — no bare ArcadeClient() in the ingestion stages (must use get_arcade_client)
# ---------------------------------------------------------------------------
def test_d538_no_bare_arcadeclient_in_ingestion():
    """No-arg ArcadeClient() ignores ARCADE_DATABASE (hardcoded 'grace'); the
    ingestion stages must construct via get_arcade_client(). AST-based so it ignores
    the capture-the-why comments that mention the old pattern."""
    offenders: list[str] = []
    for py in (REPO / "src" / "ingestion").rglob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "ArcadeClient"
                    and not node.args and not node.keywords):
                offenders.append(f"{py.relative_to(REPO)}:{node.lineno}")
    assert not offenders, f"bare ArcadeClient() found (use get_arcade_client): {offenders}"


# ---------------------------------------------------------------------------
# D539 — EmlAdapter captures raw_headers; calendar detector reads Content-Type
# ---------------------------------------------------------------------------
def test_d539_eml_adapter_captures_raw_headers(tmp_path):
    import src.ingestion.adapters  # noqa: F401
    from src.ingestion.adapter_registry import get_adapter
    from src.ingestion.models import EmlSourceConfig

    eml = (
        "From: A <a@x.example>\r\nTo: B <b@y.example>\r\n"
        "Subject: hi\r\nMessage-ID: <m1@x.example>\r\n"
        "Auto-Submitted: auto-replied\r\n"
        "Date: Mon, 02 Jun 2026 09:00:00 -0400\r\n\r\nbody\r\n"
    )
    (tmp_path / "m1.eml").write_bytes(eml.encode())
    cfg = EmlSourceConfig(directory_path=str(tmp_path))
    adapter = get_adapter("eml", cfg)

    async def _go():
        await adapter.connect(cfg)
        async for mid in adapter.list_messages():
            return (await adapter.parse_message(mid)).event

    ev = asyncio.run(_go())
    assert ev.raw_headers, "raw_headers must be captured (was null -> dead T1 detectors)"
    assert ev.raw_headers.get("Auto-Submitted") == "auto-replied"


def test_d539_calendar_detector_reads_content_type_header():
    from src.ingestion.communications.triage.tier1_noise import check_calendar_invite
    from src.ingestion.models import CommunicationEvent

    ev = CommunicationEvent(
        source_id=__import__("uuid").uuid4(),
        message_id="<cal@x.example>",
        sender_email="a@x.example",
        source_type="eml",
        raw_headers={"Content-Type": "text/calendar; method=REQUEST; charset=utf-8"},
    )
    assert check_calendar_invite(ev) == "filtered_t1_calendar_invite"


# ---------------------------------------------------------------------------
# D540 — Tier 2 entity types configurable
# ---------------------------------------------------------------------------
def test_d540_tier2_uses_configured_entity_types():
    from src.ingestion.communications.triage.config import Tier2Config
    from src.ingestion.communications.triage import tier2_entities as t2
    from src.ingestion.models import CommunicationEvent

    cfg = Tier2Config(entity_types=["Legal_Entity"])
    queries: list[str] = []

    class _Arcade:
        async def execute_cypher(self, q, params=None):
            queries.append(q)
            return {"result": [{"gid": "x"}]}  # match

    ev = CommunicationEvent(
        source_id=__import__("uuid").uuid4(), message_id="<m@x>",
        sender_email="e@x.example", sender_display_name="Alice", source_type="eml",
    )
    outcome = asyncio.run(t2.run_tier2(ev, _Arcade(), config=cfg))
    assert outcome is None, "a match against the configured type must pass T2"
    assert any("Legal_Entity" in q for q in queries), "T2 must query the configured label"
    assert not any("Person" in q for q in queries), "must NOT query the hardcoded default"


def test_d540_default_entity_types_are_superset_with_legal_entity():
    """C1 defect #3: the default vertex-label list must be a SUPERSET of the shipped
    D430 pair — Person/Organization deployments keep working, and legal-ontology
    deployments (Legal_Entity, no Person) match out of the box. Covers the triage
    Tier2Config default, the tier2 module fallback, the corroboration config default,
    and the shipped YAML keys."""
    import yaml

    from src.ingestion.communications.corroboration_scorer import CorroborationConfig
    from src.ingestion.communications.triage import tier2_entities as t2
    from src.ingestion.communications.triage.config import Tier2Config

    expected = ["Person", "Organization", "Legal_Entity"]
    assert Tier2Config().entity_types == expected
    assert list(t2._DEFAULT_ENTITY_TYPES) == expected
    assert CorroborationConfig().sender_entity_types == expected

    triage_yaml = yaml.safe_load((REPO / "config" / "triage_rules.yaml").read_text())
    assert triage_yaml["tier2"]["entity_types"] == expected

    corr_yaml = yaml.safe_load((REPO / "config" / "corroboration_config.yaml").read_text())
    assert corr_yaml["sender_entity_types"] == expected


def test_d540_yaml_tier2_entity_types_reach_triage_config(tmp_path):
    """entity_types set in the triage YAML must survive load_triage_config()."""
    from src.ingestion.communications.triage.config import load_triage_config

    cfg_file = tmp_path / "triage.yaml"
    cfg_file.write_text(
        "tier1:\n  rule_order: [duplicate_message_id]\n"
        "tier2:\n  entity_types: [Legal_Entity]\n"
    )
    cfg = load_triage_config(cfg_file)
    assert cfg.tier2.entity_types == ["Legal_Entity"]


# ---------------------------------------------------------------------------
# D541 — supersession write awaits the cypher (no fire-and-forget / loop-closed)
# ---------------------------------------------------------------------------
def test_d541_supersession_write_awaits_and_resets_pool():
    from src.ingestion.communications.supersession import _apply_supersession_write

    client = MagicMock()
    client.execute_cypher = AsyncMock(return_value={"result": []})
    client.reset_pool = MagicMock()

    # Normal sync path: no running loop. Must complete the await without raising.
    _apply_supersession_write(client, "old-gid", "new-gid", None)
    client.execute_cypher.assert_awaited_once()
    client.reset_pool.assert_called_once()


def test_d541_async_one_loop_apply_awaits_writes_without_reset_pool():
    """The production one-loop path (apply_thread_supersession_async) awaits each
    supersession write on the current loop with a shared client — no per-call
    reset_pool, so no orphaned httpx clients (D541 round-2 refactor)."""
    from src.ingestion.communications.supersession import apply_thread_supersession_async

    client = MagicMock()
    client.execute_cypher = AsyncMock(return_value={"result": []})
    # F-0032/ISS-0036: supersession now keys by entity identity — the two
    # vertices must carry the same entity_name to count as a contradiction
    # (nameless same-type pairs are conservatively refused).
    entities = [
        {"grace_id": "g1", "entity_type": "T", "thread_position": 0,
         "entity_name": "Same Entity",
         "properties": {"p": "value-A"}, "sent_at": None},
        {"grace_id": "g2", "entity_type": "T", "thread_position": 1,
         "entity_name": "Same Entity",
         "properties": {"p": "value-B"}, "sent_at": None},
    ]
    result = asyncio.run(apply_thread_supersession_async("thr", entities, client))
    assert result["superseded_count"] == 1, "a contradicting later value must supersede"
    client.execute_cypher.assert_awaited_once()
    assert not client.reset_pool.called, "async one-loop path must not reset_pool per call"


# ---------------------------------------------------------------------------
# D542 — corroboration promotion + echo-dedup
# ---------------------------------------------------------------------------
def _mk_corr(grace_id, person_ids):
    from src.ingestion.communications.corroboration_scorer import (
        EntityCorroboration, SourceMention,
    )
    return EntityCorroboration(
        entity_grace_id=grace_id, entity_type="Legal_Entity",
        mentions=[
            SourceMention(person_id=p, person_category="canonical", stance="affirm",
                          quality_key="clear_assertion", message_id=f"m{i}")
            for i, p in enumerate(person_ids)
        ],
    )


def test_d542_promotes_multi_source_not_single_or_echo():
    from src.ingestion.communications.corroboration_scorer import (
        CorroborationConfig, score_entity_v1,
    )
    cfg = CorroborationConfig()
    two = score_entity_v1(_mk_corr("e1", ["A", "B"]), cfg)
    one = score_entity_v1(_mk_corr("e2", ["A"]), cfg)
    echo = score_entity_v1(_mk_corr("e3", ["A", "A"]), cfg)

    assert two.status == "first_class" and two.corroborating_sender_count == 2
    assert one.status == "provisional", "single-source must NOT promote"
    # echo: high noisy-OR score but distinct senders = 1 -> NO false promotion
    assert echo.corroborating_sender_count == 1 and echo.status == "provisional"


def _batched_session(senders):
    """Mock Session whose batched ANY(:mids) lookup returns
    (mid, sender, body, display_name) rows — F2-07 added the display-name
    column for graph-fallback sender resolution."""
    class _Session:
        def execute(self, _stmt, params):
            mids = params["mids"]
            return MagicMock(
                fetchall=lambda: [
                    (mid, senders[mid], "we proceed", None) for mid in mids
                ]
            )
    return _Session()


def test_d542_gather_traces_provenance_to_senders():
    """gather_communication_corroborations wires graph entity -> produced_by ->
    Extraction_Event source_document_id -> communication_events sender -> registry,
    using BATCHED provenance + Postgres queries (no N+1)."""
    from src.ingestion.communications import corroboration_scorer as cs

    async def _execute_cypher(q, params=None):
        if "evidence_origin" in q:
            return {"result": [{"gid": "ent-1", "type": "Legal_Entity"}]}
        if "produced_by" in q:  # batched: returns gid + sd per provenance edge
            return {"result": [{"gid": "ent-1", "sd": "email:<m1@x>"},
                               {"gid": "ent-1", "sd": "email:<m2@y>"}]}
        # F2-07: sender resolution now binds $needle (email first, display
        # name second) instead of $email.
        email = (params or {}).get("needle")
        return {"result": [{"gid": f"person-{email}"}]}

    arcade = MagicMock()
    arcade.execute_cypher = AsyncMock(side_effect=_execute_cypher)
    senders = {"<m1@x>": "alice@x.example", "<m2@y>": "bob@y.example"}

    cfg = cs.CorroborationConfig()
    diag: dict = {}
    corrs = asyncio.run(
        cs.gather_communication_corroborations(arcade, _batched_session(senders), cfg, diag=diag)
    )
    assert len(corrs) == 1
    assert corrs[0].distinct_person_ids() == {"person-alice@x.example", "person-bob@y.example"}
    score = cs.score_entity_v1(corrs[0], cfg)
    assert score.status == "first_class", "2 distinct resolved senders -> promote"


def test_d542_skips_entity_with_no_provenance_and_records_diag():
    """An entity with no produced_by provenance must be skipped (not crash) and
    counted in diagnostics — so a silently-degraded run is distinguishable from a
    clean zero (the silent-failure class the C1 fixes exist to kill)."""
    from src.ingestion.communications import corroboration_scorer as cs

    async def _execute_cypher(q, params=None):
        if "evidence_origin" in q:
            return {"result": [{"gid": "ent-noprov", "type": "Legal_Entity"}]}
        if "produced_by" in q:
            return {"result": []}  # no provenance
        return {"result": []}

    arcade = MagicMock()
    arcade.execute_cypher = AsyncMock(side_effect=_execute_cypher)
    diag: dict = {}
    corrs = asyncio.run(
        cs.gather_communication_corroborations(arcade, MagicMock(), cs.CorroborationConfig(), diag=diag)
    )
    assert corrs == []
    assert diag.get("skipped_no_provenance") == 1


def test_d542_unresolved_senders_do_not_promote_and_are_counted():
    """If senders don't resolve to a registry vertex they stay 'unknown'; 2 distinct
    unknowns score below theta (prior 0.50) -> NOT promoted. The unknowns are counted
    so an empty/mis-keyed registry (-> nothing ever promotes) is visible, not silent."""
    from src.ingestion.communications import corroboration_scorer as cs

    async def _execute_cypher(q, params=None):
        if "evidence_origin" in q:
            return {"result": [{"gid": "ent-1", "type": "Legal_Entity"}]}
        if "produced_by" in q:
            return {"result": [{"gid": "ent-1", "sd": "email:<m1@x>"},
                               {"gid": "ent-1", "sd": "email:<m2@y>"}]}
        return {"result": []}  # sender resolution: NO match -> unknown

    arcade = MagicMock()
    arcade.execute_cypher = AsyncMock(side_effect=_execute_cypher)
    senders = {"<m1@x>": "alice@x.example", "<m2@y>": "bob@y.example"}

    diag: dict = {}
    corrs = asyncio.run(
        cs.gather_communication_corroborations(
            arcade, _batched_session(senders), cs.CorroborationConfig(), diag=diag)
    )
    assert len(corrs) == 1
    score = cs.score_entity_v1(corrs[0], cs.CorroborationConfig())
    assert score.status == "provisional", "2 UNKNOWN senders (prior 0.5) must not promote"
    assert diag.get("unresolved_senders") == 2


def test_d542_run_corroboration_binds_promote_path(monkeypatch):
    """Bind the full run flow: run_corroboration must call promote_entity for a
    2-distinct-sender entity and NOT for a single-sender one. Guards against the run
    flow silently regressing to a stub (CI-green-but-broken, the original C1 failure)."""
    from src.ingestion.communications import corroboration_scorer as cs

    two = _mk_corr("ent-2", ["A", "B"])
    one = _mk_corr("ent-1", ["A"])

    async def _gather(*a, **k):
        return [two, one]

    promoted_ids: list[str] = []

    async def _promote(grace_id, status, sender_count, *, dry_run=False):
        promoted_ids.append(grace_id)

    monkeypatch.setattr(cs, "gather_communication_corroborations", _gather)
    monkeypatch.setattr(cs, "promote_entity", _promote)
    monkeypatch.setattr(cs, "get_arcade_client", lambda: MagicMock(aclose=AsyncMock()), raising=False)
    monkeypatch.setattr(
        "src.shared.database.get_session_factory",
        lambda: (lambda: MagicMock(close=MagicMock())),
    )

    result = asyncio.run(cs.run_corroboration(dry_run=False, config=cs.CorroborationConfig()))
    assert promoted_ids == ["ent-2"], "only the 2-distinct-sender entity promotes"
    assert result["promoted"] == 1 and result["provisional"] == 1 and result["scored"] == 2
