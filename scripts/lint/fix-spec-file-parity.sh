#!/usr/bin/env bash
# fix-spec-file-parity.sh — auto-repair §2↔§6 CP Files parity in a spec.
#
# For each file path in §6 CP **Files:** lines that is missing from §2,
# determine which CP it belongs to, whether it's Created or Edited (based
# on disk existence), and append a bullet to the correct §2 subsection.
#
# Usage: bash scripts/lint/fix-spec-file-parity.sh <spec-path> [repo-root]
# Exit 0 with count of repairs, exit 1 on fatal error.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <spec-path> [repo-root]" >&2
  exit 1
fi

SPEC="$1"
REPO_ROOT="${2:-$(cd "$(dirname "$0")/../.." && pwd)}"

if [[ ! -f "$SPEC" ]]; then
  echo "ERROR: spec file not found: $SPEC" >&2
  exit 1
fi

# Step 1: Run the check script to get the list of missing files.
CHECK_SCRIPT="$(cd "$(dirname "$0")" && pwd)/check-spec-file-parity.sh"
MISSING_OUTPUT=""
if bash "$CHECK_SCRIPT" "$SPEC" 2>&1; then
  echo "fix-spec-file-parity: no repairs needed"
  exit 0
else
  MISSING_OUTPUT="$(bash "$CHECK_SCRIPT" "$SPEC" 2>&1 || true)"
fi

# Parse missing file paths from check output (lines like "  - src/foo/bar.py")
MISSING_FILES=()
while IFS= read -r line; do
  fpath="$(echo "$line" | sed 's/^[[:space:]]*-[[:space:]]*//')"
  [[ -z "$fpath" ]] && continue
  MISSING_FILES+=("$fpath")
done < <(echo "$MISSING_OUTPUT" | grep '^ *- ')

if [[ ${#MISSING_FILES[@]} -eq 0 ]]; then
  echo "fix-spec-file-parity: no repairs needed"
  exit 0
fi

# Step 2: For each missing file, find which CP it belongs to by scanning §6.
# We scan the spec for CP headings and their **Files:** lines.
get_cp_for_file() {
  local target="$1"
  local current_cp=""
  while IFS= read -r line; do
    if echo "$line" | grep -qE '^###[[:space:]]+CP[[:space:]]?[0-9]+'; then
      current_cp="$(echo "$line" | grep -oE 'CP[[:space:]]?[0-9]+' | head -1 | tr -d ' ')"
    fi
    if echo "$line" | grep -qE '^\*\*Files:\*\*'; then
      if echo "$line" | grep -qF "$target"; then
        echo "$current_cp"
        return
      fi
    fi
  done < "$SPEC"
  echo ""
}

# Step 3: Classify and build insertion lines.
EDITED_LINES=()
CREATED_BACKEND_LINES=()
CREATED_FRONTEND_LINES=()
CREATED_DOCS_LINES=()

for fpath in "${MISSING_FILES[@]}"; do
  cp_tag="$(get_cp_for_file "$fpath")"
  local_basename="$(basename "$fpath")"
  tag_suffix=""
  [[ -n "$cp_tag" ]] && tag_suffix=" *[$cp_tag]*"
  desc="— ${local_basename}${tag_suffix}"

  if [[ -f "$REPO_ROOT/$fpath" ]]; then
    EDITED_LINES+=("- \`$fpath\` $desc")
  else
    case "$fpath" in
      frontend/*) CREATED_FRONTEND_LINES+=("- \`$fpath\` $desc") ;;
      docs/*)     CREATED_DOCS_LINES+=("- \`$fpath\` $desc") ;;
      *)          CREATED_BACKEND_LINES+=("- \`$fpath\` $desc") ;;
    esac
  fi
done

TOTAL_REPAIRS=0

# Step 4: Inject lines into the correct §2 subsection.
# Strategy: find the anchor heading, then the next heading after it,
# insert lines just before that next heading.
inject_lines() {
  local anchor_pattern="$1"
  shift
  local lines_to_add=("$@")

  if [[ ${#lines_to_add[@]} -eq 0 ]]; then
    return
  fi

  # Find the anchor line number
  local anchor_line
  anchor_line="$(grep -n "$anchor_pattern" "$SPEC" | head -1 | cut -d: -f1)"
  if [[ -z "$anchor_line" ]]; then
    return
  fi

  # Find the next ## or ### heading after the anchor
  local next_heading_rel
  next_heading_rel="$(tail -n +"$((anchor_line + 1))" "$SPEC" \
    | grep -n '^##' | head -1 | cut -d: -f1 || true)"

  local insert_at
  if [[ -n "$next_heading_rel" ]]; then
    insert_at=$((anchor_line + next_heading_rel - 1))
  else
    insert_at=$(($(wc -l < "$SPEC" | tr -d ' ') + 1))
  fi

  # Build the text block to insert
  local tmpfile
  tmpfile="$(mktemp)"
  local i=0
  while IFS= read -r existing_line; do
    i=$((i + 1))
    if [[ $i -eq $insert_at ]]; then
      for new_line in "${lines_to_add[@]}"; do
        echo "$new_line"
      done
    fi
    echo "$existing_line"
  done < "$SPEC" > "$tmpfile"

  # Handle insert-at-end case
  if [[ $insert_at -gt $i ]]; then
    for new_line in "${lines_to_add[@]}"; do
      echo "$new_line" >> "$tmpfile"
    done
  fi

  mv "$tmpfile" "$SPEC"
  TOTAL_REPAIRS=$((TOTAL_REPAIRS + ${#lines_to_add[@]}))
}

# Inject Edited entries into §2.3
inject_lines '### .*2\.3.*[Ee]dited' "${EDITED_LINES[@]}"

# Inject Created Backend entries
if [[ ${#CREATED_BACKEND_LINES[@]} -gt 0 ]]; then
  if grep -q '### .*2\.2.*[Cc]reated.*[Bb]ackend' "$SPEC"; then
    inject_lines '### .*2\.2.*[Cc]reated.*[Bb]ackend' "${CREATED_BACKEND_LINES[@]}"
  elif grep -q '### .*2\.1.*[Cc]reated' "$SPEC"; then
    inject_lines '### .*2\.1.*[Cc]reated' "${CREATED_BACKEND_LINES[@]}"
  fi
fi

# Inject Created Frontend entries
if [[ ${#CREATED_FRONTEND_LINES[@]} -gt 0 ]]; then
  if grep -q '### .*2\.1.*[Cc]reated.*[Ff]rontend' "$SPEC"; then
    inject_lines '### .*2\.1.*[Cc]reated.*[Ff]rontend' "${CREATED_FRONTEND_LINES[@]}"
  elif grep -q '### .*2\.1.*[Cc]reated' "$SPEC"; then
    inject_lines '### .*2\.1.*[Cc]reated' "${CREATED_FRONTEND_LINES[@]}"
  fi
fi

# Inject Created Docs entries
if [[ ${#CREATED_DOCS_LINES[@]} -gt 0 ]]; then
  if grep -q '### .*2\.1a.*[Cc]reated.*[Dd]ocs' "$SPEC"; then
    inject_lines '### .*2\.1a.*[Cc]reated.*[Dd]ocs' "${CREATED_DOCS_LINES[@]}"
  elif grep -q '### .*2\.1.*[Cc]reated' "$SPEC"; then
    inject_lines '### .*2\.1.*[Cc]reated' "${CREATED_DOCS_LINES[@]}"
  fi
fi

# Step 5: Verify the fix worked
if bash "$CHECK_SCRIPT" "$SPEC" >/dev/null 2>&1; then
  echo "fix-spec-file-parity: repaired $TOTAL_REPAIRS missing §2 entries in $(basename "$SPEC") — parity verified"
else
  echo "fix-spec-file-parity: inserted $TOTAL_REPAIRS entries but parity check still fails — manual review needed" >&2
  exit 1
fi

for fpath in "${MISSING_FILES[@]}"; do
  echo "  + $fpath"
done
exit 0
