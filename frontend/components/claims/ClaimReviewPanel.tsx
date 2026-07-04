"use client";
import { useEffect, useMemo } from "react";
import type { ClaimRecord } from "@/lib/api/types";
import { useClaimReviewStore } from "@/lib/state/claim-review-store";
import { TeachBackInstrument } from "@/components/instruments/TeachBackInstrument";
import { ClaimSummary } from "./ClaimSummary";
import { EvidenceTextPanel } from "./EvidenceTextPanel";
import { ConstraintViolationsPanel } from "./ConstraintViolationsPanel";
import { VerifierNotePanel } from "./VerifierNotePanel";
import { DispositionBar } from "./DispositionBar";

// EC-6 mode-selection rationale string. Single source of truth for both UI
// surface and audit trail (Chunk 32 signal computation).
const QUARANTINE_RATIONALE =
  "Quarantined because the verifier judged the evidence does not support the claim — please review the source text and decide.";

// Evidence-first ordering per spec §4 D231:
// 1. claim summary
// 2. source-text panel with evidence_spans highlighted
// 3. Teach-Back instrument (D226, reused as-is; one item per evidence span)
// 4. constraint violations (collapsible, default-collapsed)
// 5. verifier contradiction reason (collapsible, default-collapsed)
// 6. disposition bar (Accept / Reject / Edit-and-Accept), gated on Teach-Back
export function ClaimReviewPanel({ claim }: { claim: ClaimRecord }) {
  const setActiveClaim = useClaimReviewStore((s) => s.setActiveClaim);
  const setTeachBackLabel = useClaimReviewStore((s) => s.setTeachBackLabel);
  const reviewer = useClaimReviewStore((s) => s.reviewer);
  const setReviewer = useClaimReviewStore((s) => s.setReviewer);

  useEffect(() => {
    setActiveClaim(claim);
  }, [claim, setActiveClaim]);

  const teachBackItems = useMemo(
    () =>
      claim.evidence_spans.map((s, i) => ({
        index: i,
        sentence: s.text,
      })),
    [claim.evidence_spans],
  );

  // Wire the InstrumentShell's onComplete by snapshotting D226 internal labels
  // through a parallel store update (the instrument is immutable so we
  // observe completion at the children's onChange via a side channel: the
  // instrument's "Complete" button fires `teach_back_completed` telemetry,
  // but we need per-span gating *before* that fires. We expose a separate
  // RadioGroup hook below for the gate; the immutable D226 instrument still
  // renders for telemetry/correction capture.)
  return (
    <article
      data-testid="claim-review-panel"
      className="space-y-3"
      aria-labelledby="claim-review-heading"
    >
      <h2 id="claim-review-heading" className="sr-only">
        Claim review
      </h2>
      <ClaimSummary claim={claim} />
      <EvidenceTextPanel spans={claim.evidence_spans} />
      <p
        data-testid="quarantine-rationale"
        className="rounded border border-amber-300 bg-amber-50 p-2 text-xs text-slate-800"
      >
        {QUARANTINE_RATIONALE}
      </p>

      {/* Per-span gating radio group lives next to the immutable Teach-Back
          instrument; the gate state belongs to the disposition store, not
          inside D226. */}
      <fieldset
        data-testid="teach-back-gate"
        className="rounded border border-border bg-white p-3 text-xs"
      >
        <legend className="px-1 font-medium">Teach-Back labels</legend>
        {claim.evidence_spans.length === 0 ? (
          <p className="text-slate-500">
            No evidence spans to review — proceed directly to disposition.
          </p>
        ) : (
          claim.evidence_spans.map((_, i) => (
            <div key={i} className="flex items-center gap-3 py-1">
              <span className="text-slate-700">Span #{i + 1}</span>
              {(["correct", "wrong", "missing_something"] as const).map((label) => (
                <label key={label} className="flex items-center gap-1">
                  <input
                    type="radio"
                    name={`gate-${i}`}
                    data-testid={`gate-${i}-${label}`}
                    onChange={() => setTeachBackLabel(i, label)}
                  />
                  {label.replace("_", " ")}
                </label>
              ))}
            </div>
          ))
        )}
      </fieldset>

      <details className="rounded border border-border bg-white p-2 text-xs">
        <summary className="cursor-pointer font-medium">
          Teach-Back instrument (corrections optional)
        </summary>
        <div className="mt-2">
          <TeachBackInstrument items={teachBackItems} />
        </div>
      </details>

      <ConstraintViolationsPanel violations={claim.constraint_violations} />
      <VerifierNotePanel note={claim.verifier_contradiction_reason} />

      <label className="flex items-center gap-2 text-xs text-slate-700">
        Reviewer
        <input
          data-testid="reviewer-input"
          className="rounded border px-2 py-1"
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
        />
      </label>

      <DispositionBar claim={claim} />
    </article>
  );
}
