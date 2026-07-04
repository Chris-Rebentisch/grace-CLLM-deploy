"use client";

/**
 * HypothesisDecisionBar — D322 / EC-12 mirror.
 *
 * Five action paths over the Layer 4 hypothesis surface:
 *   - accepted_segmented      (Accept this hypothesis)
 *   - accepted_null           (Accept the null hypothesis)
 *   - rerun_finer             (Re-run with finer resolution)
 *   - rerun_coarser           (Re-run with coarser resolution)
 *   - reject_all_reformulate  (Reject all — reformulate)  [free-text rationale]
 *
 * Reject-all opens an inline rationale dialog before firing the callback
 * so the caller can persist a human-authored rationale string.
 *
 * EC-12 copy discipline: no forbidden tokens in any button label or
 * dialog body.
 */

import { useState } from "react";

export type HypothesisDecisionKind =
  | "accepted_segmented"
  | "accepted_null"
  | "rerun_finer"
  | "rerun_coarser"
  | "reject_all_reformulate";

export type HypothesisDecisionBarProps = {
  /** Disable the entire bar (e.g. while a decision is in flight). */
  disabled?: boolean;
  /**
   * Fires once the operator commits to a decision. For
   * `reject_all_reformulate` the rationale string is also passed.
   */
  onDecide: (kind: HypothesisDecisionKind, rationale?: string) => void;
};

export function HypothesisDecisionBar({
  disabled = false,
  onDecide,
}: HypothesisDecisionBarProps) {
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rationale, setRationale] = useState("");

  const click = (kind: HypothesisDecisionKind) => {
    if (kind === "reject_all_reformulate") {
      setRejectOpen(true);
      return;
    }
    onDecide(kind);
  };

  return (
    <div
      data-testid="hypothesis-decision-bar"
      className="flex flex-wrap gap-2 rounded-md border border-slate-200 bg-white p-3 text-xs"
    >
      <button
        type="button"
        disabled={disabled}
        onClick={() => click("accepted_segmented")}
        data-testid="decision-accepted-segmented"
        className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1 text-emerald-900 hover:bg-emerald-100 disabled:opacity-50"
      >
        Accept this hypothesis
      </button>
      <button
        type="button"
        disabled={disabled}
        onClick={() => click("accepted_null")}
        data-testid="decision-accepted-null"
        className="rounded border border-slate-300 bg-slate-50 px-2 py-1 text-slate-900 hover:bg-slate-100 disabled:opacity-50"
      >
        Accept the null hypothesis
      </button>
      <button
        type="button"
        disabled={disabled}
        onClick={() => click("rerun_finer")}
        data-testid="decision-rerun-finer"
        className="rounded border border-blue-300 bg-blue-50 px-2 py-1 text-blue-900 hover:bg-blue-100 disabled:opacity-50"
      >
        Re-run with finer resolution
      </button>
      <button
        type="button"
        disabled={disabled}
        onClick={() => click("rerun_coarser")}
        data-testid="decision-rerun-coarser"
        className="rounded border border-blue-300 bg-blue-50 px-2 py-1 text-blue-900 hover:bg-blue-100 disabled:opacity-50"
      >
        Re-run with coarser resolution
      </button>
      <button
        type="button"
        disabled={disabled}
        onClick={() => click("reject_all_reformulate")}
        data-testid="decision-reject-all"
        className="rounded border border-rose-300 bg-rose-50 px-2 py-1 text-rose-900 hover:bg-rose-100 disabled:opacity-50"
      >
        Reject all — reformulate
      </button>

      {rejectOpen ? (
        <div
          data-testid="reject-rationale-dialog"
          role="dialog"
          aria-label="Provide rationale to reformulate"
          className="mt-2 w-full rounded-md border border-rose-200 bg-white p-2"
        >
          <label
            className="mb-1 block text-xs font-medium text-slate-700"
            htmlFor="reject-rationale-input"
          >
            Rationale for reformulating Layer 4
          </label>
          <textarea
            id="reject-rationale-input"
            data-testid="reject-rationale-input"
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            className="mb-2 block w-full rounded border border-slate-300 p-1 text-xs"
            rows={3}
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              data-testid="reject-rationale-cancel"
              onClick={() => {
                setRejectOpen(false);
                setRationale("");
              }}
              className="rounded border border-slate-300 bg-white px-2 py-0.5 text-slate-700"
            >
              Cancel
            </button>
            <button
              type="button"
              data-testid="reject-rationale-submit"
              disabled={rationale.trim().length === 0}
              onClick={() => {
                onDecide("reject_all_reformulate", rationale.trim());
                setRejectOpen(false);
                setRationale("");
              }}
              className="rounded border border-rose-400 bg-rose-50 px-2 py-0.5 text-rose-900 disabled:opacity-50"
            >
              Submit reformulation request
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
