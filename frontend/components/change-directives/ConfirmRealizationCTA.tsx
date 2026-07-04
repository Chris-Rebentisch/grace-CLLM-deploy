"use client";

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiClient } from "@/lib/api/client";
import type { RealizationSnapshotPayload } from "@/lib/api/types";
import { changeDirectiveActorHeaders } from "@/lib/api/change-directives";

function isoDay(d: string) {
  return d.slice(0, 10);
}

export function ConfirmRealizationCTA({
  directiveId,
  tier,
  status,
  effectiveDate,
  latest,
  actorUserId,
}: {
  directiveId: string;
  tier: "Operational_Adjustment" | "Strategic_Initiative";
  status: string;
  effectiveDate?: string | null;
  latest: RealizationSnapshotPayload | null;
  actorUserId: string;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const hdrs = changeDirectiveActorHeaders(actorUserId);

  const siReady =
    tier === "Strategic_Initiative" &&
    status === "active" &&
    latest?.criteria_all_satisfied === true;

  const today = isoDay(new Date().toISOString());
  const oaReady =
    tier === "Operational_Adjustment" &&
    status === "active" &&
    !!effectiveDate &&
    isoDay(effectiveDate) <= today &&
    !!latest?.last_counter_evidence_seen_at &&
    isoDay(latest.last_counter_evidence_seen_at) < isoDay(effectiveDate);

  const show = siReady || oaReady;
  if (!show) return null;

  async function confirm() {
    setBusy(true);
    try {
      await apiClient.transitionChangeDirective(
        directiveId,
        { to_state: "realized", reason: "operator_confirmed_realization" },
        hdrs,
      );
      setOpen(false);
      window.location.reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        data-testid="confirm-realization-cta"
        className="rounded bg-slate-900 px-3 py-1.5 text-xs font-medium text-white"
        onClick={() => setOpen(true)}
      >
        Confirm realization
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Mark as realized?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-slate-600">
            This records an explicit transition to the realized state. It does not run
            automatically from evidence alone.
          </p>
          <DialogFooter>
            <button
              type="button"
              className="rounded border px-3 py-1 text-sm"
              onClick={() => setOpen(false)}
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={busy}
              className="rounded bg-slate-900 px-3 py-1 text-sm text-white disabled:opacity-50"
              onClick={() => void confirm()}
            >
              {busy ? "Working…" : "Transition to realized"}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
