# Reviewer Provider Recovery Runbook

**D491 — Chunk 75b**

## Symptoms

| Symptom | Likely Cause |
|---------|-------------|
| `preflight-code.sh` reports "one reviewer provider degraded" | Single provider CLI unreachable |
| `preflight-code.sh` reports "both reviewer providers degraded" | Both CLIs unreachable; blocks code-stage launch |
| `check-reviewer-providers.sh` exits 1 or 2 | Degraded or fully degraded |

## Diagnostics

1. **Check cursor-agent availability:**
   ```bash
   cursor-agent --version
   ```
   If command not found: Cursor IDE is not installed, or `cursor-agent` is not on PATH.

2. **Check Claude CLI availability:**
   ```bash
   claude --version
   ```
   If command not found: Claude Code CLI is not installed, or `claude` is not on PATH.

3. **Check cached probe result:**
   ```bash
   cat ~/grace/.build-state/reviewer-provider-health.json
   ```
   The `timestamp` field shows when the last probe ran. Cache TTL is 60 seconds.

## Short-Term Fallback

If one provider is degraded but the other is healthy:

- The pipeline can proceed using the healthy provider.
- D479 auto-fallback (chunk 74): if `reviewer_fallback_command_template` is configured in `config/pipeline_automation.json`, the orchestrator automatically retries failed reviewer subprocesses with the fallback provider after `reviewer_fallback_min_consecutive_exhausted` (default 3) consecutive failures.

Check whether D479 is active:
```bash
grep 'reviewer_fallback_command_template' ~/grace/config/pipeline_automation.json
```

## Extended-Outage Config Swap

If both providers are degraded and recovery is not imminent:

1. **Verify the underlying tools are installed:**
   ```bash
   which cursor-agent
   which claude
   ```

2. **Reinstall if needed:**
   - Cursor IDE: download from cursor.com, ensure CLI is enabled in settings.
   - Claude Code: `npm install -g @anthropic-ai/claude-code`

3. **Force cache refresh:**
   ```bash
   rm -f ~/grace/.build-state/reviewer-provider-health.json
   bash scripts/pipeline/check-reviewer-providers.sh
   ```

4. **If using mock provider for development:**
   ```bash
   python3 scripts/pipeline/run_pipeline.py run --chunk <N> --provider mock
   ```
   Mock provider does not require cursor-agent or Claude CLI.

## Recovery Confirmation

After restoring provider(s):

```bash
# Clear stale cache
rm -f ~/grace/.build-state/reviewer-provider-health.json

# Re-probe
bash scripts/pipeline/check-reviewer-providers.sh
echo "Exit code: $?"

# Verify preflight passes
bash scripts/pipeline/preflight-code.sh <chunk>
```

Expected: exit code 0 (both healthy) or 1 (one healthy, advisory only).
