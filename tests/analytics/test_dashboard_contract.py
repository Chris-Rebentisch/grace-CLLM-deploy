"""Dashboard contract lint — enforces the four invariants in spec §6.1.

Walks every ``*.json`` under ``docker/grafana/dashboards/``. For each
panel's ``targets[*]``, validates:

    a. Every PromQL metric name is registered in ``src.analytics.metrics``.
    b. Every placeholder-backed metric query includes the ``!="_init"``
       label filters from D151.
    c. Every panel's ``datasource.uid`` is ``${DS_PROMETHEUS}`` or
       ``${DS_POSTGRES}`` (templated variable form).
    d. Every Postgres ``rawSql`` references only allowed tables and
       columns, parsed via ``sqlglot``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

from tests.analytics.test_metric_contract import GOLDEN_NAMES


_DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "docker" / "grafana" / "dashboards"

_PLACEHOLDER_METRICS = frozenset({
    "grace_compression_ratio",
    "grace_decompression_faithfulness",
    "grace_mine_retention_ratio",
})

_REQUIRED_INIT_FILTERS = (
    'ontology_module!="_init"',
    'phase_state!="_init"',
    'sample_batch_id!="_init"',
)

_ALLOWED_UIDS = frozenset({"${DS_PROMETHEUS}", "${DS_POSTGRES}"})

# Suffixes the Prometheus exposition layer appends to counter / histogram
# family names. Strip them before matching the dashboard query's metric
# name against the registered family set.
_PROM_SUFFIXES = ("_bucket", "_count", "_sum", "_total")

_METRIC_NAME_RE = re.compile(
    r"\b(grace_[a-z_]+|http_server_[a-z_]+|gen_ai_[a-z_]+)\b"
)


_TABLE_ALLOWLIST = frozenset({
    "extraction_events_pg",
    "extraction_claims",
    "entity_resolution_log",
    "mine_samples",
    "ontology_versions",
    "schema_proposals",
    "calibration_records",
    "schema_promotion_events",
    "review_sessions",
    "review_decisions",
    "change_of_status_events",
    "cq_test_runs",
    "competency_questions",
    "cq_clusters",
    "schema_extraction_runs",
    "schema_merge_runs",
    "merge_runs",
    "processed_documents",
    "analytics_signals",
    "signal_runs",
    "correlation_runs",
    "diagnostic_records",
    "alert_events",
    "eval_runs",
    "deepeval_results",
})


def _columns_for_table(table: str) -> set[str]:
    """Column discovery from the live SQLAlchemy MetaData registries.

    Walks known database-binding modules and collects their ``MetaData``
    objects. Missing tables return an empty set, which fails assertion
    (d) because referenced columns won't be found.
    """
    from sqlalchemy import MetaData

    from src.analytics.correlation_engine import database as correlation_engine_db  # noqa: F401
    from src.analytics.signal_pipeline import database as signal_pipeline_db  # noqa: F401
    from src.discovery import cq_database, database as discovery_db  # noqa: F401
    from src.eval import results_writer as eval_results_writer  # noqa: F401
    from src.extraction import (
        claim_database,
        mine_sampler,  # noqa: F401
        resolution_database,
    )
    from src.ontology import cq_test_runner, database as ontology_db, review_database

    seen: set[int] = set()
    for module_obj in (
        claim_database,
        resolution_database,
        mine_sampler,
        ontology_db,
        review_database,
        cq_test_runner,
        cq_database,
        discovery_db,
        signal_pipeline_db,
        correlation_engine_db,
        eval_results_writer,
    ):
        for attr in dir(module_obj):
            obj = getattr(module_obj, attr)
            md = getattr(obj, "metadata", None)
            if not isinstance(md, MetaData):
                continue
            if id(md) in seen:
                continue
            seen.add(id(md))
            if table in md.tables:
                return {col.name for col in md.tables[table].columns}
    return set()


def _iter_panel_targets():
    """Yield ``(dashboard, panel, target)`` across all dashboards + panels."""
    for path in sorted(_DASHBOARD_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        for panel in data.get("panels", []):
            for target in panel.get("targets", []):
                yield path, panel, target


def _metric_families() -> frozenset[str]:
    """Allowed PromQL family name set = GOLDEN_NAMES + the ``_total`` counter forms."""
    names = set(GOLDEN_NAMES)
    # Counters emit as ``<family>_total`` in PromQL queries even though the
    # OTel exposition strips ``_total`` from the family metadata line.
    for n in list(names):
        names.add(n + "_total")
    return frozenset(names)


def _extract_metric_references(promql: str) -> set[str]:
    """Pull metric-shaped identifiers out of PromQL, ignoring labels.

    Labels appear inside ``{...}`` selectors and ``by (...)`` / ``without
    (...)`` clauses. Strip those surface forms before regex matching.
    """
    if not promql:
        return set()
    cleaned = re.sub(r"\{[^}]*\}", "", promql)
    cleaned = re.sub(
        r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return set(_METRIC_NAME_RE.findall(cleaned))


def _strip_prom_suffix(name: str) -> str:
    for suf in _PROM_SUFFIXES:
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def test_promql_metrics_are_all_registered():
    """Every metric referenced in any dashboard ``expr`` is registered."""
    allowed = _metric_families()
    offenders: list[tuple[str, str, str]] = []
    for path, _panel, target in _iter_panel_targets():
        expr = target.get("expr")
        if not expr:
            continue
        for raw in _extract_metric_references(expr):
            stripped = _strip_prom_suffix(raw)
            if stripped not in allowed and raw not in allowed:
                offenders.append((path.name, raw, expr))
    assert not offenders, (
        f"PromQL references unregistered metrics: {offenders[:5]}"
    )


def test_placeholder_metrics_include_init_filter():
    """All PromQL queries touching placeholder metrics carry the D151 filters."""
    offenders: list[tuple[str, str, str]] = []
    for path, _panel, target in _iter_panel_targets():
        expr = target.get("expr")
        if not expr:
            continue
        for placeholder in _PLACEHOLDER_METRICS:
            if placeholder not in expr:
                continue
            missing = [f for f in _REQUIRED_INIT_FILTERS if f not in expr]
            if missing:
                offenders.append((path.name, placeholder, ",".join(missing)))
    assert not offenders, (
        f"Placeholder metric queries missing _init filters: {offenders}"
    )


def test_all_panel_datasource_uids_are_templated():
    """Every ``datasource.uid`` is a ``${DS_*}`` templated variable."""
    offenders: list[tuple[str, str]] = []
    for path, panel, target in _iter_panel_targets():
        for ds in (panel.get("datasource") or {}, target.get("datasource") or {}):
            uid = ds.get("uid") if isinstance(ds, dict) else None
            if uid is None:
                continue
            if uid not in _ALLOWED_UIDS:
                offenders.append((path.name, uid))
    assert not offenders, (
        f"Hardcoded datasource UID references: {offenders}"
    )


def test_raw_sql_tables_and_columns_are_allowlisted():
    """Every ``rawSql`` parses; every table is in the preflight §1 inventory;
    every referenced column exists on the SQLAlchemy definition."""
    bad_tables: list[tuple[str, str]] = []
    bad_columns: list[tuple[str, str, str]] = []

    for path, _panel, target in _iter_panel_targets():
        raw = target.get("rawSql")
        if not raw:
            continue
        # Replace Grafana time macros with a dummy SQL-parseable stand-in so
        # sqlglot can parse; the macro name isn't in the allowlist but isn't
        # a real column either. The preflight §1 is concerned with column
        # and table identifiers.
        cleaned = raw.replace("$__timeFrom()", "now()").replace("$__timeTo()", "now()")
        try:
            parsed = sqlglot.parse_one(cleaned, read="postgres")
        except sqlglot.errors.ParseError as exc:
            pytest.fail(f"rawSql in {path.name} failed to parse: {exc}\nSQL: {raw}")

        # CTE aliases are not real tables — exclude from the table check.
        cte_aliases: set[str] = set()
        for cte in parsed.find_all(exp.CTE):
            alias = cte.alias_or_name
            if alias:
                cte_aliases.add(alias)

        referenced_tables: set[str] = set()
        for t in parsed.find_all(exp.Table):
            name = t.name
            if name and name not in cte_aliases:
                referenced_tables.add(name)

        for tbl in referenced_tables:
            if tbl not in _TABLE_ALLOWLIST:
                bad_tables.append((path.name, tbl))

        # Column checking. Union of real columns across every referenced
        # allowlist table, plus CTE-produced columns, plus any query-local
        # alias defined via ``AS <name>``. PG functions like ``count`` are
        # parsed as Columns by sqlglot when not wrapped — filter those out
        # by excluding Column nodes whose parent is a function-call node.
        union_cols: set[str] = set()
        for tbl in referenced_tables & _TABLE_ALLOWLIST:
            union_cols |= _columns_for_table(tbl)
        # Add CTE output columns — anything aliased in CTE SELECT lists.
        for cte in parsed.find_all(exp.CTE):
            for alias in cte.find_all(exp.Alias):
                name = alias.alias_or_name
                if name:
                    union_cols.add(name)
        # Add any outer-level AS aliases (these appear as column identifiers
        # in lateral references elsewhere in the query).
        for alias in parsed.find_all(exp.Alias):
            name = alias.alias_or_name
            if name:
                union_cols.add(name)

        if union_cols:
            for c in parsed.find_all(exp.Column):
                col_name = c.name
                if not col_name or col_name in union_cols:
                    continue
                parent = c.parent
                if isinstance(parent, (exp.Func, exp.Anonymous)):
                    continue
                bad_columns.append((path.name, ",".join(sorted(referenced_tables)), col_name))

    assert not bad_tables, f"SQL references non-allowlisted tables: {bad_tables}"
    assert not bad_columns, (
        f"SQL references non-existent columns: {bad_columns[:10]}"
    )
