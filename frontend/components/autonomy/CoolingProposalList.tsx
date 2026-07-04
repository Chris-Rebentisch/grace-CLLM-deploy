"use client";

/**
 * CoolingProposalList — Proposals in COOLING status with confirm/revert CTAs.
 *
 * D120/D217 — status badges and time-remaining labels, not raw
 * timestamps or confidence numbers. D194 — X-Graph-Scope: all
 * (carried by apiRequest). EC-2 — no pressure phrasing.
 */

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api/client";
import type { CoolingProposal } from "@/lib/api/types";
import { AUTONOMY_COPY } from "@/lib/autonomy/copy";

function formatExpiry(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = d.getTime() - now.getTime();
  if (diffMs <= 0) return "Expired";
  const hours = Math.floor(diffMs / 3_600_000);
  if (hours > 0) return `${hours}h remaining`;
  const mins = Math.floor(diffMs / 60_000);
  return `${mins}m remaining`;
}

export function CoolingProposalList() {
  const [proposals, setProposals] = useState<CoolingProposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [revertTarget, setRevertTarget] = useState<string | null>(null);
  const [revertReason, setRevertReason] = useState("");
  const [revertBy, setRevertBy] = useState("");
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchProposals = async () => {
    try {
      const data = await apiRequest<{
        items: CoolingProposal[];
        next_cursor: string | null;
      }>("/api/ontology/proposals?status=cooling");
      setProposals(data.items);
    } catch {
      // Swallow — empty state renders.
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProposals();
  }, []);

  const handleConfirm = async (proposalId: string) => {
    setActionLoading(proposalId);
    try {
      await apiRequest(`/api/ontology/daemon/${proposalId}/confirm`, {
        method: "POST",
      });
      setProposals((prev) => prev.filter((p) => p.id !== proposalId));
    } finally {
      setActionLoading(null);
    }
  };

  const handleRevert = async () => {
    if (!revertTarget || !revertBy) return;
    setActionLoading(revertTarget);
    try {
      await apiRequest(`/api/ontology/daemon/${revertTarget}/revert`, {
        method: "POST",
        body: { reverted_by: revertBy, reason: revertReason || null },
      });
      setProposals((prev) => prev.filter((p) => p.id !== revertTarget));
    } finally {
      setActionLoading(null);
      setRevertTarget(null);
      setRevertReason("");
      setRevertBy("");
    }
  };

  if (loading) {
    return <p className="text-xs text-slate-500">Loading…</p>;
  }

  return (
    <div data-testid="cooling-proposal-list">
      {proposals.length === 0 ? (
        <p
          data-testid="cooling-empty"
          className="text-xs italic text-slate-500"
        >
          {AUTONOMY_COPY.coolingEmpty}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {proposals.map((p) => (
            <li
              key={p.id}
              data-testid={`cooling-proposal-${p.id}`}
              className="rounded border border-slate-200 bg-white p-3"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex flex-col gap-0.5">
                  <span className="text-xs font-medium text-slate-900">
                    {AUTONOMY_COPY.coolingCommandLabel}: {p.kgcl_command}
                  </span>
                  <span className="text-[10px] text-slate-500">
                    {AUTONOMY_COPY.coolingTierLabel} {p.change_tier} · {AUTONOMY_COPY.coolingExpiresLabel}: {formatExpiry(p.cooling_period_expires_at)}
                  </span>
                </div>
                <div className="flex gap-1">
                  <button
                    data-testid={`confirm-btn-${p.id}`}
                    disabled={actionLoading === p.id}
                    onClick={() => handleConfirm(p.id)}
                    className="rounded bg-emerald-600 px-2 py-1 text-[10px] font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    {AUTONOMY_COPY.coolingConfirm}
                  </button>
                  <button
                    data-testid={`revert-btn-${p.id}`}
                    disabled={actionLoading === p.id}
                    onClick={() => setRevertTarget(p.id)}
                    className="rounded border border-rose-300 bg-rose-50 px-2 py-1 text-[10px] font-medium text-rose-700 hover:bg-rose-100 disabled:opacity-50"
                  >
                    {AUTONOMY_COPY.coolingRevert}
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {revertTarget ? (
        <div
          data-testid="revert-dialog"
          className="mt-3 rounded border border-rose-300 bg-rose-50 p-3"
        >
          <h4 className="mb-1 text-xs font-semibold text-rose-800">
            {AUTONOMY_COPY.coolingRevertDialogTitle}
          </h4>
          <p className="mb-2 text-[10px] text-rose-700">
            {AUTONOMY_COPY.coolingRevertDialogBody}
          </p>
          <label className="mb-1 block text-[10px] font-medium text-slate-700">
            {AUTONOMY_COPY.coolingRevertedBy}
          </label>
          <input
            data-testid="revert-by-input"
            type="text"
            value={revertBy}
            onChange={(e) => setRevertBy(e.target.value)}
            className="mb-2 w-full rounded border border-slate-300 px-2 py-1 text-xs"
            placeholder="operator"
          />
          <label className="mb-1 block text-[10px] font-medium text-slate-700">
            {AUTONOMY_COPY.coolingRevertReasonLabel}
          </label>
          <textarea
            data-testid="revert-reason-input"
            value={revertReason}
            onChange={(e) => setRevertReason(e.target.value)}
            rows={2}
            className="mb-2 w-full rounded border border-slate-300 px-2 py-1 text-xs"
            placeholder={AUTONOMY_COPY.coolingRevertReasonPlaceholder}
          />
          <div className="flex gap-2">
            <button
              data-testid="revert-cancel-btn"
              onClick={() => {
                setRevertTarget(null);
                setRevertReason("");
                setRevertBy("");
              }}
              className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600"
            >
              {AUTONOMY_COPY.coolingRevertCancel}
            </button>
            <button
              data-testid="revert-submit-btn"
              disabled={!revertBy || actionLoading === revertTarget}
              onClick={handleRevert}
              className="rounded bg-rose-600 px-2 py-1 text-xs font-medium text-white hover:bg-rose-700 disabled:opacity-50"
            >
              {AUTONOMY_COPY.coolingRevertSubmit}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
