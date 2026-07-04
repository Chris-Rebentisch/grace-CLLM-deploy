"""Eval suite CLI (Chunk 34, D257, D246).

Out-of-process per D246: there is no in-process scheduler. Operators
schedule via host cron / launchd. CLI subcommands:

  * ``run-suite``       — load config + golden dataset, run DeepEval suite,
                          persist via ``results_writer``. Exit codes per spec.
  * ``export-bench4ke`` — write Bench4KE-shaped CSV (D261).
  * ``show-config``     — print resolved YAML config (with defaults).
  * ``validate-golden`` — run loader assertions only.

Exit codes (run-suite):
  0  — every case passed every metric's fail-floor.
  1  — any case breached a fail-floor.
  2  — unhandled / unrecoverable error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Sequence

import yaml

from src.eval.deepeval_runner import (
    EvalConfig,
    EvalRunner,
    EvalThreshold,
    _METRIC_NAMES,
)
from src.eval.golden_loader import (
    GoldenDatasetValidationError,
    default_golden_dir,
    load_golden_dataset,
)


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "eval_config.yaml"


# --- Config loading -------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Eval config missing: {path}")
    return yaml.safe_load(path.read_text()) or {}


def load_config(config_path: Path | None = None) -> EvalConfig:
    """Read ``config/eval_config.yaml`` and produce an ``EvalConfig``."""
    path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    raw = _load_yaml(path)
    thresholds_raw = raw.get("thresholds") or {}
    thresholds: dict[str, EvalThreshold] = {}
    for name in _METRIC_NAMES:
        block = thresholds_raw.get(name) or {}
        thresholds[name] = EvalThreshold(
            warn_floor=float(block.get("warn_floor", 0.80)),
            fail_floor=float(block.get("fail_floor", 0.70)),
            higher_is_better=(name != "hallucination"),
        )
    judge = raw.get("judge") or {}
    return EvalConfig(
        thresholds=thresholds,
        judge_provider=judge.get("provider", "ollama"),
        judge_model=judge.get("model", "qwen2.5:7b"),
        judge_base_url=judge.get("base_url", "http://localhost:11434"),
        per_case_timeout_seconds=int(raw.get("per_case_timeout_seconds", 60)),
        golden_dataset_dir=raw.get("golden_dataset_dir"),
    )


# --- DB preflight (R20) ---------------------------------------------------


def _eval_tables_present() -> bool:
    """Return True iff ``eval_runs`` and ``deepeval_results`` exist.

    Used by ``run-suite`` to short-circuit with a guidance message when
    the c34 migration has not been applied yet.
    """
    try:
        from sqlalchemy import inspect

        from src.shared.database import get_engine

        inspector = inspect(get_engine())
        names = set(inspector.get_table_names())
        return "eval_runs" in names and "deepeval_results" in names
    except Exception:  # noqa: BLE001 — surface as missing-table at the call site
        return False


# --- Subcommand handlers --------------------------------------------------


async def _cmd_run_suite(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    golden_dir = (
        Path(args.golden_dir)
        if args.golden_dir
        else (
            Path(config.golden_dataset_dir)
            if config.golden_dataset_dir
            else default_golden_dir()
        )
    )

    try:
        cases = load_golden_dataset(golden_dir)
    except GoldenDatasetValidationError as exc:
        print(json.dumps({"status": "invalid_golden_dataset", "error": str(exc)}))
        return 1

    if args.dry_run:
        # Dry-run never touches the DB; preflight is irrelevant.
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "total_cases": len(cases),
                    "metrics": list(_METRIC_NAMES),
                }
            )
        )
        return 0

    if not _eval_tables_present():
        print(
            json.dumps(
                {
                    "status": "missing_tables",
                    "error": (
                        "eval_runs / deepeval_results not present. "
                        "Run `alembic upgrade head` before `run-suite`."
                    ),
                }
            )
        )
        return 1

    from src.eval.deepeval_runner import run_suite as run_suite_fn
    from src.eval.results_writer import (
        hash_config,
        hash_golden_dataset,
        write_run,
    )
    from src.shared.database import get_session_factory

    cfg_hash = hash_config(config)
    gold_hash = hash_golden_dataset(cases)

    runner = EvalRunner()
    run = await runner.run_suite(
        cases,
        config,
        triggered_by="cli",
        config_hash=cfg_hash,
        golden_dataset_hash=gold_hash,
    )

    session_factory = get_session_factory()
    session = session_factory()
    try:
        write_run(session, run)
        session.commit()
    finally:
        session.close()

    print(
        json.dumps(
            {
                "run_id": str(run.id),
                "status": run.status,
                "total_cases": run.total_cases,
                "passed_warn_floor": run.passed_warn_floor,
                "passed_fail_floor": run.passed_fail_floor,
            }
        )
    )

    if run.status == "success":
        return 0
    return 1


def _cmd_export_bench4ke(args: argparse.Namespace) -> int:
    from src.eval.bench4ke_export import export_to_csv

    output = Path(args.output)
    n = export_to_csv(output, include_unverified=args.include_unverified)
    print(json.dumps({"status": "ok", "rows_written": n, "output": str(output)}))
    return 0


def _cmd_show_config(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    payload = {
        "thresholds": {
            name: {
                "warn_floor": t.warn_floor,
                "fail_floor": t.fail_floor,
                "higher_is_better": t.higher_is_better,
            }
            for name, t in config.thresholds.items()
        },
        "judge": {
            "provider": config.judge_provider,
            "model": config.judge_model,
            "base_url": config.judge_base_url,
        },
        "per_case_timeout_seconds": config.per_case_timeout_seconds,
        "golden_dataset_dir": config.golden_dataset_dir,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _cmd_validate_golden(args: argparse.Namespace) -> int:
    config = load_config(args.config) if args.config else None
    if args.golden_dir:
        golden_dir = Path(args.golden_dir)
    elif config and config.golden_dataset_dir:
        golden_dir = Path(config.golden_dataset_dir)
    else:
        golden_dir = default_golden_dir()
    try:
        cases = load_golden_dataset(golden_dir)
    except GoldenDatasetValidationError as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}))
        return 1
    print(json.dumps({"status": "valid", "total_cases": len(cases)}))
    return 0


# --- Argparse plumbing ----------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grace-eval",
        description="GrACE evaluation suite CLI (Chunk 34, D257).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run-suite", help="Run the DeepEval regression suite.")
    p_run.add_argument("--config", default=None, help="Path to YAML config.")
    p_run.add_argument("--golden-dir", default=None, help="Override golden dataset directory.")
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Print case count + metric set without touching the DB or judge.",
    )
    p_run.add_argument("--verbose", "-v", action="store_true")

    p_export = sub.add_parser(
        "export-bench4ke", help="Write a Bench4KE-shaped CSV from competency_questions."
    )
    p_export.add_argument("--output", required=True, help="Output CSV path.")
    p_export.add_argument(
        "--include-unverified",
        action="store_true",
        help="Include CQs whose verification_status is not PASS / HUMAN_CONFIRMED.",
    )

    p_show = sub.add_parser("show-config", help="Print the resolved eval config.")
    p_show.add_argument("--config", default=None)

    p_val = sub.add_parser("validate-golden", help="Validate the golden dataset.")
    p_val.add_argument("--config", default=None)
    p_val.add_argument("--golden-dir", default=None)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "run-suite":
            return asyncio.run(_cmd_run_suite(args))
        if args.command == "export-bench4ke":
            return _cmd_export_bench4ke(args)
        if args.command == "show-config":
            return _cmd_show_config(args)
        if args.command == "validate-golden":
            return _cmd_validate_golden(args)
    except SystemExit:
        raise
    except Exception:
        logging.exception("eval_cli_unrecoverable_error")
        return 2

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
