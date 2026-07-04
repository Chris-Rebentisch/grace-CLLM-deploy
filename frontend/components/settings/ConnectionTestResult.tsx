"use client";
import type { TestLLMConfigResponse } from "@/lib/api/types";

export function ConnectionTestResult({
  result,
}: {
  result: TestLLMConfigResponse | null;
}) {
  if (!result) return null;
  return (
    <dl
      data-testid="connection-test-result"
      className="grid grid-cols-2 gap-y-1 rounded border bg-white p-3 text-xs"
    >
      <dt>Healthy</dt>
      <dd data-testid="conn-healthy">{String(result.healthy)}</dd>
      <dt>Model available</dt>
      <dd data-testid="conn-model-available">{String(result.model_available)}</dd>
      <dt>Provider</dt>
      <dd data-testid="conn-provider">{result.provider}</dd>
      <dt>Model</dt>
      <dd data-testid="conn-model">{result.model}</dd>
      <dt>Response</dt>
      <dd data-testid="conn-response" className="truncate">
        {result.test_response || "—"}
      </dd>
      <dt>Error</dt>
      <dd data-testid="conn-error" className="text-rose-700">
        {result.error || "—"}
      </dd>
    </dl>
  );
}
