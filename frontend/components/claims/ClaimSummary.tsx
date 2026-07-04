"use client";
import type { ClaimRecord } from "@/lib/api/types";

// D120 / D217: render claim subject + predicate + object as a sentence with
// no verdict label and no numeric confidence. The verifier verdict is
// surfaced separately by the VerifierNotePanel; this component is purely
// the canonical "what does the LLM say happened" sentence.
export function ClaimSummary({ claim }: { claim: ClaimRecord }) {
  const sentence = [
    claim.subject_name,
    claim.predicate ?? (claim.entity_type ? "is a" : ""),
    claim.object_name ?? (claim.entity_type ?? ""),
  ]
    .filter(Boolean)
    .join(" ")
    .trim();

  return (
    <section
      data-testid="claim-summary"
      className="rounded border border-border bg-white p-3 text-sm text-slate-800"
      aria-label="Claim summary"
    >
      {sentence}
    </section>
  );
}
