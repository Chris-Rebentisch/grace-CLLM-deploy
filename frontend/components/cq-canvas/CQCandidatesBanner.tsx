"use client";
import { useCQCandidates, useCQCandidateDecision } from "@/lib/query/cq";

export function CQCandidatesBanner({ sessionId }: { sessionId: string }) {
  const { data: candidates, isLoading } = useCQCandidates(sessionId);
  const { mutate: decide } = useCQCandidateDecision();

  const quarantined = (candidates ?? []).filter((c) => (c as Record<string,unknown>).validation_status === "quarantined");

  if (isLoading) return <div data-testid="cq-candidates-banner" className="text-xs text-slate-400 p-2">Loading candidates...</div>;
  if (quarantined.length === 0) return null;

  return (
    <div data-testid="cq-candidates-banner" className="rounded-md border border-amber-200 bg-amber-50 p-2">
      <div className="mb-1 text-xs font-medium text-amber-800">{quarantined.length} quarantined candidate{quarantined.length !== 1 ? "s" : ""}</div>
      {quarantined.map((c) => {
        const candidate = c as Record<string, unknown>;
        return (
          <div key={String(candidate.id)} className="mb-1 flex items-center gap-2 text-xs" data-testid={`candidate-${String(candidate.id)}`}>
            <span className="flex-1">{String(candidate.cq_text)}</span>
            <span data-testid={`candidate-source-${String(candidate.id)}`} className="rounded bg-slate-200 px-1 text-[10px]">{String(candidate.source_origin)}</span>
            <button type="button" onClick={() => decide({ id: String(candidate.id), action: "accept" })} className="rounded bg-green-100 px-1 text-[10px] text-green-700">Accept</button>
            <button type="button" onClick={() => decide({ id: String(candidate.id), action: "reject" })} className="rounded bg-red-100 px-1 text-[10px] text-red-700">Reject</button>
          </div>
        );
      })}
    </div>
  );
}
