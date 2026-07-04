#!/usr/bin/env bash
# Chunk 43 D346 lint — mandatory-context flip for cypher_rewriter.rewrite().
#
# Replaces the Chunk 42 D335 opt-in lint. Two assertions:
#
#   1. Every in-tree call to ``cypher_rewriter.rewrite(...)`` (any file
#      under the scanned roots that imports it) passes ``principal=``
#      explicitly. Files that do not import ``cypher_rewriter`` are
#      skipped — the AST scan is keyed on the import substring to avoid
#      false positives on unrelated functions named ``rewrite``.
#
#   2. No file under ``$CHECK_CYPHER_API_ROOT`` (default ``src/api``)
#      imports ``SystemPrincipal`` / ``SYSTEM_PRINCIPAL`` (R12). HTTP
#      route handlers must resolve principals via
#      ``from_admission_tree(request)``, not smuggle the system sentinel
#      that bypasses agent-scope intersection.
#
# Usage:
#   bash scripts/lint/check-cypher-principal-context.sh [src-root ...]
#
# Default scan root: ``src``. Tests under ``tests/`` are not scanned by
# default — the failure-mode tests in
# ``tests/permissions/test_cypher_rewriter_mandatory_context.py`` call
# rewrite() without principal= deliberately to assert ``TypeError``.

set -uo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

if [[ $# -gt 0 ]]; then
    roots=("$@")
else
    roots=("src")
fi

api_root="${CHECK_CYPHER_API_ROOT:-src/api}"

violations=0

# ---- Assertion 1: every rewrite() caller passes principal= explicitly ----

if ! python3 - "${roots[@]}" <<'PY'
import ast
import pathlib
import sys

roots = sys.argv[1:]
violations = 0
for root in roots:
    base = pathlib.Path(root)
    if not base.exists():
        continue
    for path in base.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "cypher_rewriter" not in text:
            continue
        # The rewriter source file itself defines rewrite(); skip.
        if path.as_posix().endswith("permissions/cypher_rewriter.py"):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            is_rewrite = (
                (isinstance(fn, ast.Name) and fn.id == "rewrite")
                or (isinstance(fn, ast.Attribute) and fn.attr == "rewrite")
            )
            if not is_rewrite:
                continue
            kws = {kw.arg for kw in node.keywords}
            if "principal" not in kws:
                print(
                    f"[D346 lint] missing principal= at "
                    f"{path}:{node.lineno}"
                )
                violations += 1
sys.exit(1 if violations else 0)
PY
then
    violations=$((violations + 1))
fi

# ---- Assertion 2: src/api/* must not import SystemPrincipal (R12) -------
# AST-based — matching only real import statements (Import / ImportFrom)
# avoids false positives from docstrings or comments that mention the
# class name.

if ! python3 - "$api_root" <<'PY'
import ast
import pathlib
import sys

api_root = pathlib.Path(sys.argv[1])
violations = 0
if api_root.is_dir():
    for path in sorted(api_root.glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.endswith("system_principal"):
                    print(
                        f"[D346 lint] R12 violation: {path} imports "
                        f"from {mod}"
                    )
                    violations += 1
                else:
                    for alias in node.names:
                        if alias.name in ("SystemPrincipal", "SYSTEM_PRINCIPAL"):
                            print(
                                f"[D346 lint] R12 violation: {path} "
                                f"imports {alias.name} from {mod}"
                            )
                            violations += 1
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.endswith("system_principal"):
                        print(
                            f"[D346 lint] R12 violation: {path} imports "
                            f"{alias.name}"
                        )
                        violations += 1
sys.exit(1 if violations else 0)
PY
then
    violations=$((violations + 1))
fi

if [[ $violations -gt 0 ]]; then
    echo "[D346 lint] $violations violation(s) found."
    exit 1
fi

echo "[D346 lint] OK"
exit 0
