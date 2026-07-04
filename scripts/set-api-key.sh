#!/bin/bash
# Sets LLM_API_KEY (your Anthropic API key) in the project's .env.
# Resolves the project root from this script's own location, so it works
# regardless of where the repo is checked out.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"

echo ""
echo "=== GrACE API Key Setup ==="
echo ""
echo "Paste your Anthropic API key below and press Enter."
echo "(The key starts with sk-ant-)"
echo ""
read -r -p "API Key: " api_key

if [[ -z "$api_key" ]]; then
    echo "No key entered. Nothing changed."
    exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
    echo "No .env found at $ENV_FILE — copy .env.example to .env first."
    exit 1
fi

if grep -q "^LLM_API_KEY=" "$ENV_FILE"; then
    # macOS sed syntax
    sed -i '' "s|^LLM_API_KEY=.*|LLM_API_KEY=${api_key}|" "$ENV_FILE"
else
    echo "LLM_API_KEY=${api_key}" >> "$ENV_FILE"
fi

echo ""
echo "Done! API key saved to $ENV_FILE"
echo ""
# Show first chars only to confirm (never print the full key)
preview=$(grep "^LLM_API_KEY=" "$ENV_FILE" | cut -c1-25)
echo "Verification: ${preview}..."
echo ""
