"""D544 guard — the email extraction bridge must construct a fully-wired
ExtractionPipeline.

Regression for Finding #11 (interface drift, same class as D539/D543): the bridge
body called ``ExtractionPipeline()`` with no arguments while the constructor
requires ``config, chunker, router, client``. Every ``extraction_bridge run``
raised ``TypeError: missing 4 required positional arguments`` before processing a
single email — the sanctioned email front door was dead-on-arrival despite a green
test suite (the prior tests mocked ``_process_email`` / the pipeline and never
exercised real construction).

These tests are heat-free: ``_build_pipeline`` constructs dependencies but issues
no LLM call, and the AST sweep reads source only.
"""

import ast
import inspect
from pathlib import Path

from src.extraction import extraction_bridge
from src.extraction.extraction_pipeline import ExtractionPipeline


def test_build_pipeline_returns_wired_pipeline():
    """_build_pipeline must return a real ExtractionPipeline without raising."""
    pipeline = extraction_bridge._build_pipeline(arcade_client=None)
    assert isinstance(pipeline, ExtractionPipeline)
    # All four required collaborators must be populated.
    assert pipeline._config is not None
    assert pipeline._chunker is not None
    assert pipeline._router is not None
    assert pipeline._client is not None


def test_build_pipeline_honors_base_url_env(monkeypatch):
    """The production OntologyRouter must point at MCP_GRACE_BASE_URL (D246 client)."""
    monkeypatch.setenv("MCP_GRACE_BASE_URL", "http://127.0.0.1:9999")
    pipeline = extraction_bridge._build_pipeline(arcade_client=None)
    assert pipeline._router._base_url == "http://127.0.0.1:9999"


def test_d545_run_subcommand_accepts_module_flag():
    """D545 — the run subcommand must expose a --module knob (multi-module deployments)."""
    parser = extraction_bridge._build_argparser()
    ns = parser.parse_args(["run", "--module", "legal", "--limit", "3"])
    assert ns.module == "legal"
    # default is None (single-module deployments unchanged)
    ns_default = parser.parse_args(["run"])
    assert ns_default.module is None


def test_d545_module_name_threaded_to_extract_document():
    """D545 — _process_email must forward module_name into extract_document."""
    sig = inspect.signature(extraction_bridge._process_email)
    assert "module_name" in sig.parameters
    sig_run = inspect.signature(extraction_bridge.run_bridge)
    assert "module_name" in sig_run.parameters
    # source-level: extract_document call passes module_name=
    src = Path(inspect.getfile(extraction_bridge)).read_text()
    assert "module_name=module_name" in src


def test_d547_bridge_uses_settings_aware_arcade_client():
    """D547 — the bridge must NOT call bare ArcadeClient() (ignores ARCADE_DATABASE).

    Bare ArcadeClient() hardcodes ArcadeConfig().database='grace' and so wrote
    email-derived vertices into the GOLD graph during sandbox testing. The bridge
    must use get_arcade_client() (settings-aware) so ARCADE_DATABASE isolation holds.
    """
    src = Path(inspect.getfile(extraction_bridge)).read_text()
    tree = ast.parse(src)
    bare_calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ArcadeClient"
            and not node.args
            and not node.keywords
        ):
            bare_calls.append(node.lineno)
    assert not bare_calls, (
        f"bare ArcadeClient() at line(s) {bare_calls} ignores ARCADE_DATABASE — "
        "use get_arcade_client() so sandbox isolation holds (D547)."
    )


def test_d547_get_arcade_client_honors_arcade_database(monkeypatch):
    """D547 — get_arcade_client() must route to the configured ARCADE_DATABASE."""
    import src.shared.config as shared_config
    from src.graph.arcade_client import get_arcade_client

    monkeypatch.setenv("ARCADE_DATABASE", "grace_test")
    # get_settings() may be cached; clear so the env override is read.
    if hasattr(shared_config.get_settings, "cache_clear"):
        shared_config.get_settings.cache_clear()
    client = get_arcade_client()
    assert client.config.database == "grace_test"
    if hasattr(shared_config.get_settings, "cache_clear"):
        shared_config.get_settings.cache_clear()


def test_no_zero_arg_pipeline_construction_in_source():
    """AST sweep: the bridge must never call ExtractionPipeline() with zero args.

    Catches a regression to the Finding #11 shape directly in source so the guard
    holds even if the runtime path is mocked in other tests.
    """
    src = Path(inspect.getfile(extraction_bridge)).read_text()
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ExtractionPipeline"
            and not node.args
            and not node.keywords
        ):
            offenders.append(node.lineno)
    assert not offenders, (
        f"ExtractionPipeline() called with no arguments at line(s) {offenders} "
        "— constructor requires config/chunker/router/client (D544)."
    )
