"use client";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";

export function CQAuthoringForm({ sessionId }: { sessionId: string }) {
  const [text, setText] = useState("");
  const [result, setResult] = useState<"pass" | "fail" | null>(null);

  const validateMutation = useMutation({
    mutationFn: async (cqText: string) => {
      const cq = await apiClient.createCQ({ text: cqText, session_id: sessionId, source: "user" });
      const testResult = await apiClient.post<Record<string, unknown>>("/api/ontology/cq-test/run", { schema_version_id: null, concurrency: 1 });
      return testResult;
    },
    onSuccess: (data) => { setResult(data?.status === "failed" ? "fail" : "pass"); },
    onError: () => { setResult("fail"); },
  });

  return (
    <div data-testid="cq-authoring-form" className="rounded-md border border-border p-2">
      <div className="mb-1 text-xs font-medium text-slate-700">Add CQ</div>
      <div className="flex gap-2">
        <input type="text" value={text} onChange={(e) => setText(e.target.value)} placeholder="Enter competency question..." className="flex-1 rounded border border-border px-2 py-1 text-xs" data-testid="cq-authoring-input" />
        <button type="button" onClick={() => { if (text.trim()) validateMutation.mutate(text); }} disabled={validateMutation.isPending || !text.trim()} className="rounded bg-slate-800 px-3 py-1 text-xs text-white disabled:opacity-50" data-testid="cq-authoring-submit">
          {validateMutation.isPending ? "Validating..." : "Add"}
        </button>
      </div>
      {result && (
        <div data-testid="cq-authoring-result" className={`mt-1 text-xs ${result === "pass" ? "text-green-600" : "text-red-600"}`}>
          CQ Test Runner: {result === "pass" ? "PASS" : "FAIL"}
        </div>
      )}
    </div>
  );
}
