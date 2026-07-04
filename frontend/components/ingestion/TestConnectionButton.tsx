"use client";

import { useState } from "react";
import { INGESTION_COPY } from "@/lib/ingestion/copy";

interface TestConnectionButtonProps {
  sourceId: string;
}

interface TestResult {
  ok: boolean;
  sample_message_count: number | null;
  sample_date_range: { oldest: string; newest: string } | null;
  error: string | null;
}

export function TestConnectionButton({ sourceId }: TestConnectionButtonProps) {
  const [result, setResult] = useState<TestResult | null>(null);
  const [loading, setLoading] = useState(false);

  const handleTest = async () => {
    setLoading(true);
    try {
      const resp = await fetch(`/api/ingestion/sources/${sourceId}/test`, {
        method: "POST",
        headers: { "X-Graph-Scope": "all" },
      });
      const data = await resp.json();
      setResult(data);
    } catch {
      setResult({ ok: false, sample_message_count: null, sample_date_range: null, error: "Network error" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-2">
      <button
        onClick={handleTest}
        disabled={loading}
        className="rounded border px-4 py-2 text-sm hover:bg-gray-50"
      >
        {loading ? "Testing..." : INGESTION_COPY.testConnectionButton}
      </button>
      {result && (
        <div className={`rounded border p-3 text-sm ${result.ok ? "border-green-300 bg-green-50" : "border-red-300 bg-red-50"}`}>
          <p className="font-medium">
            {result.ok ? INGESTION_COPY.testConnectionSuccess : INGESTION_COPY.testConnectionFailure}
          </p>
          {result.ok && result.sample_message_count !== null && (
            <p className="text-gray-600">
              {result.sample_message_count} sample messages found
            </p>
          )}
          {result.error && <p className="text-red-600">{result.error}</p>}
        </div>
      )}
    </div>
  );
}
