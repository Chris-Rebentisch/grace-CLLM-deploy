#!/usr/bin/env bash
# scripts/lint/check-migration-revision-ids.sh
#
# CI lint enforcing Alembic revision id <= 32 chars (PostgreSQL
# alembic_version.version_num is VARCHAR(32)). Walks alembic/versions/*.py,
# extracts `revision: str = "..."` (and `revision = "..."`) literals, fails
# fast if any exceed 32 characters or if `down_revision` references a string
# that is also too long.
#
# Background: chunk-43 catastrophic C1 (`c42d_hypothesis_one_running_per_evidence`
# = 41 chars > VARCHAR(32)) shipped to disk before alembic upgrade head ran.
# The same chunk independently reproduced the bug at c43a (38 chars). Without
# this lint, the failure mode is a ~$10 wasted code-author session. See
# `docs/pipeline-failure-recovery-runbook.md` Pattern A.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Optional argument: a single-file or single-directory override (used by
# fixture-based pytest harnesses). Default scans the repo's alembic tree.
TARGET="${1:-$REPO_ROOT/alembic/versions}"

if [[ ! -e "$TARGET" ]]; then
  echo "check-migration-revision-ids: target missing: $TARGET" >&2
  exit 2
fi

python3 - "$TARGET" <<'PY'
import re
import sys
from pathlib import Path

target = Path(sys.argv[1])
files = []
if target.is_dir():
    files = sorted(p for p in target.glob("*.py") if not p.name.startswith("__"))
elif target.is_file() and target.suffix == ".py":
    files = [target]
else:
    print(f"check-migration-revision-ids: not a .py file or versions dir: {target}", file=sys.stderr)
    sys.exit(2)

if not files:
    print(f"check-migration-revision-ids: OK (no migration files under {target})")
    sys.exit(0)

# Match either `revision: str = "abc"` or `revision = "abc"` and similarly
# `down_revision: ... = "abc"` (down_revision may also be a tuple/None — we
# only flag when the value is a single string literal).
RE_REV = re.compile(r'^revision(?:\s*:\s*[A-Za-z_][\w\[\], ]*)?\s*=\s*"([^"]+)"', re.MULTILINE)
RE_DOWN = re.compile(r'^down_revision(?:\s*:\s*[A-Za-z_][\w\[\], ]*)?\s*=\s*"([^"]+)"', re.MULTILINE)

LIMIT = 32
violations = []
total = 0

for f in files:
    text = f.read_text(encoding="utf-8")
    for m in RE_REV.finditer(text):
        total += 1
        rev = m.group(1)
        if len(rev) > LIMIT:
            violations.append((f, "revision", rev, len(rev)))
    for m in RE_DOWN.finditer(text):
        rev = m.group(1)
        if len(rev) > LIMIT:
            violations.append((f, "down_revision", rev, len(rev)))

if violations:
    print(f"check-migration-revision-ids: FAIL ({len(violations)} revision id(s) exceed {LIMIT} chars)")
    for path, kind, rev, length in violations:
        rel = path.relative_to(path.anchor) if not path.is_absolute() else path
        print(f"  - {path}: {kind}=\"{rev}\" ({length} chars > {LIMIT})")
    print()
    print("Remediation: shorten the revision id to <= 32 chars in the migration file's")
    print("`revision: str = \"...\"` literal (and any sibling `down_revision = \"...\"`)")
    print("references). Optional: rename the .py file for hygiene. Then run:")
    print("  alembic upgrade head")
    print("  psql grace -c 'SELECT version_num FROM alembic_version;'")
    print("See docs/pipeline-failure-recovery-runbook.md Pattern A for the full recipe.")
    sys.exit(1)

print(f"check-migration-revision-ids: OK ({total} revision literal(s) checked across {len(files)} file(s); all <= {LIMIT} chars)")
PY
