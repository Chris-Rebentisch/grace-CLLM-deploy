"use client";
import type { ClaimEvidenceSpan } from "@/lib/api/types";

// Renders the source text panel with evidence_spans highlighted inline.
// When the spans aren't anchored in a single source-text snippet (Chunk 30
// scope: the API returns evidence-span text only, not the surrounding
// document), fall back to listing each span as a labelled excerpt.
export function EvidenceTextPanel({ spans }: { spans: ClaimEvidenceSpan[] }) {
  if (spans.length === 0) {
    return (
      <section
        data-testid="evidence-empty"
        className="rounded border border-dashed border-border p-3 text-xs text-slate-500"
        aria-label="Evidence spans"
      >
        No evidence spans were captured for this claim.
      </section>
    );
  }
  return (
    <section
      data-testid="evidence-text-panel"
      className="space-y-2 rounded border border-border bg-white p-3 text-sm text-slate-800"
      aria-label="Evidence spans"
    >
      {spans.map((s, i) => (
        <blockquote
          key={`${s.start_char}-${s.end_char}-${i}`}
          data-testid={`evidence-span-${i}`}
          className="border-l-4 border-amber-300 bg-amber-50 px-3 py-1 text-xs italic text-slate-700"
        >
          {s.text}
        </blockquote>
      ))}
    </section>
  );
}
