#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/lint/check-compose-mounts.sh <artifact-path>" >&2
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
compose_files = [
    repo / "docker/docker-compose.observability.yml",
    repo / "docker/docker-compose.arcade.yml",
]

if not artifact.exists():
    print(f"check-compose-mounts: artifact missing: {artifact}", file=sys.stderr)
    sys.exit(2)

artifact_text = artifact.read_text(encoding="utf-8")
compose_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in compose_files if p.exists())
host_mounts = set()
for line in compose_text.splitlines():
    m = re.search(r"-\s+(\./[A-Za-z0-9_./-]+):", line.strip())
    if m:
        host_mounts.add(m.group(1).removeprefix("./"))

# Track likely mount-sensitive references used in docs/prompts/specs.
refs = set(re.findall(r"\b(?:docker/)?grafana/[A-Za-z0-9_./-]+\b", artifact_text))
refs |= set(re.findall(r"\b(?:docker/)?prometheus/[A-Za-z0-9_./-]+\b", artifact_text))

missing = []
for ref in sorted(refs):
    # Normalize optional docker/ prefix for matching compose text.
    stripped = ref[7:] if ref.startswith("docker/") else ref
    covered = any(stripped.startswith(m) for m in host_mounts)
    if not covered and stripped not in compose_text and ref not in compose_text:
        missing.append(ref)

if missing:
    print("check-compose-mounts: FAIL path(s) not represented in compose mounts/config:")
    for m in missing:
        print(f"  - {m}")
    sys.exit(1)

print(f"check-compose-mounts: OK ({len(refs)} references checked)")
PY
