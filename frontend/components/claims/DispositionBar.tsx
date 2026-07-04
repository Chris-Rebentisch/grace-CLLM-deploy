"use client";
import { useState } from "react";
import type { ClaimRecord, AcceptClaimModified } from "@/lib/api/types";
import { useClaimReviewStore } from "@/lib/state/claim-review-store";
import { useAcceptClaim, useRejectClaim } from "@/lib/query/claims";
import { sha256Hex } from "@/lib/ids/hash";
import { useSessionStore } from "@/lib/state/session-store";
import { emitTelemetry } from "@/lib/telemetry/bus";
import { EditAndAcceptForm } from "./EditAndAcceptForm";

// D231 + Buçinca 2021 cognitive forcing function: disposition gates on
// Teach-Back completion of every evidence span. With zero spans the gate is
// trivially satisfied (spec §4 D231 edge case).
export function DispositionBar({ claim }: { claim: ClaimRecord }) {
  const isComplete = useClaimReviewStore((s) => s.isTeachBackComplete());
  const reviewer = useClaimReviewStore((s) => s.reviewer);
  const editFormOpen = useClaimReviewStore((s) => s.editFormOpen);
  const setEditFormOpen = useClaimReviewStore((s) => s.setEditFormOpen);
  const sessionId = useSessionStore((s) => s.sessionId);
  const activePhase = useSessionStore((s) => s.activePhase);

  const acceptMut = useAcceptClaim();
  const rejectMut = useRejectClaim();
  const [pending, setPending] = useState(false);

  const disabled = !isComplete || pending || reviewer.trim() === "";

  const fireDisposition = async (
    action: "accept" | "reject",
    modified?: AcceptClaimModified,
  ) => {
    if (!sessionId) return;
    setPending(true);
    const claimIdHash = await sha256Hex(claim.claim_id);
    const reviewerHash = await sha256Hex(reviewer);
    try {
      if (action === "accept") {
        await acceptMut.mutateAsync({
          claimId: claim.claim_id,
          body: { reviewer, modified_claim: modified ?? null },
        });
        emitTelemetry("claim_disposition_accepted", {
          claim_id_hash: claimIdHash,
          reviewer_hash: reviewerHash,
          was_modified: Boolean(modified),
          ontology_module: claim.ontology_module ?? "core",
        });
      } else {
        await rejectMut.mutateAsync({
          claimId: claim.claim_id,
          body: { reviewer },
        });
        emitTelemetry("claim_disposition_rejected", {
          claim_id_hash: claimIdHash,
          reviewer_hash: reviewerHash,
          ontology_module: claim.ontology_module ?? "core",
        });
      }
    } finally {
      setPending(false);
      setEditFormOpen(false);
    }
    void activePhase;
  };

  if (editFormOpen) {
    return (
      <EditAndAcceptForm
        claim={claim}
        onSubmit={(mod) => void fireDisposition("accept", mod)}
        onCancel={() => setEditFormOpen(false)}
      />
    );
  }

  return (
    <div
      data-testid="disposition-bar"
      className="flex flex-wrap items-center gap-2 rounded border border-border bg-slate-50 p-3"
      aria-label="Claim disposition"
    >
      {!isComplete && (
        <p className="text-xs text-slate-600" data-testid="disposition-gate-hint">
          Complete the Teach-Back labels above before choosing a disposition.
        </p>
      )}
      {reviewer.trim() === "" && (
        <p className="text-xs text-slate-600" data-testid="disposition-reviewer-hint">
          Enter your reviewer name to enable disposition.
        </p>
      )}
      <button
        type="button"
        data-testid="disposition-accept"
        disabled={disabled}
        onClick={() => void fireDisposition("accept")}
        className="rounded bg-emerald-700 px-3 py-1 text-xs text-white disabled:opacity-50"
      >
        Accept
      </button>
      <button
        type="button"
        data-testid="disposition-reject"
        disabled={disabled}
        onClick={() => void fireDisposition("reject")}
        className="rounded bg-rose-700 px-3 py-1 text-xs text-white disabled:opacity-50"
      >
        Reject
      </button>
      <button
        type="button"
        data-testid="disposition-edit-and-accept"
        disabled={disabled}
        onClick={() => setEditFormOpen(true)}
        className="rounded bg-blue-700 px-3 py-1 text-xs text-white disabled:opacity-50"
      >
        Edit and Accept
      </button>
    </div>
  );
}
