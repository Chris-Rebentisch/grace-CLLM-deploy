"use client";
import { useState } from "react";
import type { ClaimRecord } from "@/lib/api/types";
import { ClaimList } from "@/components/claims/ClaimList";
import { ClaimReviewPanel } from "@/components/claims/ClaimReviewPanel";

export default function ClaimsPage() {
  const [active, setActive] = useState<ClaimRecord | null>(null);
  return (
    <div className="grid h-full grid-cols-[18rem_1fr] gap-3 p-3" data-testid="claims-page">
      <aside className="rounded border border-border bg-white">
        <ClaimList onSelect={setActive} selectedClaimId={active?.claim_id ?? null} />
      </aside>
      <main className="overflow-y-auto">
        {active ? (
          <ClaimReviewPanel claim={active} />
        ) : (
          <p className="p-4 text-sm text-slate-500" data-testid="claims-no-selection">
            Select a quarantined claim from the list to begin review.
          </p>
        )}
      </main>
    </div>
  );
}
