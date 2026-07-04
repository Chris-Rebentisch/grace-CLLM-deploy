"use client";
import { useState } from "react";
import type { ReviewDecisionType } from "@/lib/api/types";

const DESTRUCTIVE: Set<ReviewDecisionType> = new Set(["rejected", "merged", "split", "reclassified"]);

export type DecisionConfirmDialogProps = {
  decision: ReviewDecisionType;
  elementName: string;
  payload: Record<string, unknown>;
  onConfirm: () => void;
  onCancel: () => void;
};

export function DecisionConfirmDialog({ decision, elementName, payload, onConfirm, onCancel }: DecisionConfirmDialogProps) {
  const [typed, setTyped] = useState("");
  const isDestructive = DESTRUCTIVE.has(decision);
  const confirmText = elementName.toUpperCase();

  return (
    <div data-testid="decision-confirm-dialog" className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-96 rounded-lg bg-white p-4 shadow-xl">
        <div className="mb-2 text-sm font-semibold">{isDestructive ? "Destructive Action" : "Confirm Decision"}</div>
        <div className="mb-2 text-xs text-slate-600">
          {decision} on <strong>{elementName}</strong>
        </div>
        <div className="mb-3 rounded bg-slate-50 p-2 text-xs font-mono" data-testid="decision-payload-preview">
          {JSON.stringify(payload, null, 2)}
        </div>
        {isDestructive ? (
          <div data-testid="typed-confirmation">
            <div className="mb-1 text-xs text-red-600">Type &quot;{confirmText}&quot; to confirm:</div>
            <input type="text" value={typed} onChange={(e) => setTyped(e.target.value)} className="w-full rounded border px-2 py-1 text-xs" data-testid="typed-confirm-input" />
            <div className="mt-2 flex gap-2">
              <button type="button" onClick={onConfirm} disabled={typed !== confirmText} className="rounded bg-red-600 px-3 py-1 text-xs text-white disabled:opacity-50" data-testid="confirm-destructive-btn">Confirm</button>
              <button type="button" onClick={onCancel} className="rounded bg-slate-200 px-3 py-1 text-xs" data-testid="cancel-btn">Cancel</button>
            </div>
          </div>
        ) : (
          <div data-testid="click-confirmation" className="flex gap-2">
            <button type="button" onClick={onConfirm} className="rounded bg-slate-800 px-3 py-1 text-xs text-white" data-testid="confirm-btn">Confirm</button>
            <button type="button" onClick={onCancel} className="rounded bg-slate-200 px-3 py-1 text-xs" data-testid="cancel-btn">Cancel</button>
          </div>
        )}
      </div>
    </div>
  );
}
