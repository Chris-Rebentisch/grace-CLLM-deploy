#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/lint/check-canonical-anchors.sh <artifact-path>" >&2
  exit 2
fi

ARTIFACT="$1"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

python3 - "$ARTIFACT" "$REPO_ROOT" <<'PY'
import re
import sys
from pathlib import Path

artifact = Path(sys.argv[1])
repo = Path(sys.argv[2])
if not artifact.exists():
    print(f"check-canonical-anchors: artifact missing: {artifact}", file=sys.stderr)
    sys.exit(2)

text = artifact.read_text(encoding="utf-8")

doc_aliases = {
    "security-posture.md": repo / "docs/security-posture.md",
    "grace-decisions.md": repo / "docs/GrACE-Decisions.md",
    "grace-backlog.md": repo / "docs/GrACE-Backlog.md",
    "grace-roadmap.md": repo / "docs/GrACE-Roadmap.md",
    "grace-doc-map.md": repo / "docs/GrACE-Doc-Map.md",
    "claude.md": repo / "CLAUDE.md",
}

def load_headings(path: Path):
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if m:
            title = m.group(1).strip()
            sec = re.match(r"^(\d+(?:\.\d+)*)\b", title)
            if sec:
                out.add(sec.group(1))
    return out

headings = {k: load_headings(v) for k, v in doc_aliases.items()}

errors = []
for m in re.finditer(r"([A-Za-z0-9_.-]+\.md)\s*§\s*(\d+(?:\.\d+)*)", text, flags=re.IGNORECASE):
    doc = m.group(1).lower()
    sec = m.group(2)
    if doc not in headings:
        continue
    if sec not in headings[doc]:
        errors.append(f"missing anchor: {m.group(1)} §{sec}")

# Repeated literal consistency (CIDR/port/path families)
cidrs = re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\b", text)
ports = re.findall(r"\b(?:port|:)\s*(\d{2,5})\b", text, flags=re.IGNORECASE)
paths = re.findall(r"\b(?:docs|src|frontend|docker)/[A-Za-z0-9_./-]+\b", text)

has_constants = bool(re.search(r"##\s*3\..*Constants|##\s*3\s+Constants", text, flags=re.IGNORECASE))
if len(set(cidrs)) > 1 and not has_constants:
    errors.append("multiple CIDR values detected without §3 Constants alias block")
if len(set(ports)) > 1 and not has_constants:
    errors.append("multiple port values detected without §3 Constants alias block")
if len(set(paths)) > 1 and not has_constants:
    # Only enforce if many repeated paths are present.
    repeated = [p for p in set(paths) if paths.count(p) > 1]
    if len(repeated) >= 2:
        errors.append("multiple repeated path literals detected without §3 Constants alias block")

if errors:
    print("check-canonical-anchors: FAIL")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)

print("check-canonical-anchors: OK")
PY
