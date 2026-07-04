"use client";

/**
 * KillSwitchButton — Asymmetric-friction kill switch (D400).
 *
 * Engage (stop autonomy): requires a non-empty trimmed reason; button stays
 * disabled until the operator supplies one (admin-key path unchanged).
 * Disengage (resume autonomy): confirmation dialog required.
 *
 * D120/D217 — no numeric scores in DOM.
 * D194 — X-Graph-Scope: all (carried by apiRequest).
 */

import { useState } from "react";
import { apiRequest } from "@/lib/api/client";
import { AUTONOMY_COPY } from "@/lib/autonomy/copy";
import { emitTelemetry } from "@/lib/telemetry/bus";
import { RestorePriorStateDialog } from "./RestorePriorStateDialog";

export function KillSwitchButton({
  engaged,
  previousState,
  onToggled,
}: {
  engaged: boolean;
  previousState?: Record<string, boolean>;
  onToggled: (newState: boolean) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [reason, setReason] = useState("");

  const toggle = async (enable: boolean) => {
    setLoading(true);
    try {
      await apiRequest<{ autonomy_enabled: boolean; tiers_updated: number }>(
        "/api/ontology/daemon/kill-switch",
        { method: "PATCH", body: { autonomy_enabled: enable, reason } },
      );
      emitTelemetry(
        enable ? "kill_switch_disengaged" : "kill_switch_engaged",
        enable
          ? { disengaged_by: "operator" }
          : { engaged_by: "operator", reason },
      );
      onToggled(enable);
      setReason("");
    } finally {
      setLoading(false);
      setShowConfirm(false);
    }
  };

  // Engaged = autonomy stopped. Button to resume (disengage) needs confirmation.
  if (engaged) {
    return (
      <div data-testid="kill-switch" className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <span
            data-testid="kill-switch-status"
            className="inline-block rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-medium text-rose-700"
          >
            {AUTONOMY_COPY.killSwitchStatusStopped}
          </span>
          <button
            data-testid="kill-switch-disengage-btn"
            disabled={loading}
            onClick={() => setShowConfirm(true)}
            className="rounded border border-emerald-300 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50"
          >
            {AUTONOMY_COPY.killSwitchDisengage}
          </button>
        </div>
        {showConfirm ? (
          previousState ? (
            <RestorePriorStateDialog
              previousState={previousState}
              loading={loading}
              onConfirm={() => toggle(true)}
              onCancel={() => setShowConfirm(false)}
            />
          ) : (
            <div
              data-testid="kill-switch-confirm-dialog"
              className="rounded border border-amber-300 bg-amber-50 p-3"
            >
              <p className="mb-2 text-xs text-amber-800">
                {AUTONOMY_COPY.killSwitchDisengageConfirm}
              </p>
              <div className="flex gap-2">
                <button
                  data-testid="kill-switch-confirm-cancel"
                  onClick={() => setShowConfirm(false)}
                  className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600"
                >
                  {AUTONOMY_COPY.killSwitchDisengageCancel}
                </button>
                <button
                  data-testid="kill-switch-confirm-resume"
                  disabled={loading}
                  onClick={() => toggle(true)}
                  className="rounded bg-emerald-600 px-2 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                >
                  {AUTONOMY_COPY.killSwitchDisengageConfirmButton}
                </button>
              </div>
            </div>
          )
        ) : null}
      </div>
    );
  }

  // Not engaged = autonomy active. Single-click engage (stop).
  return (
    <div data-testid="kill-switch" className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span
          data-testid="kill-switch-status"
          className="inline-block rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700"
        >
          {AUTONOMY_COPY.killSwitchStatusActive}
        </span>
        <button
          data-testid="kill-switch-engage-btn"
          disabled={loading || !reason.trim()}
          onClick={() => toggle(false)}
          className="rounded border border-rose-300 bg-rose-50 px-3 py-1 text-xs font-medium text-rose-700 hover:bg-rose-100 disabled:opacity-50"
        >
          {AUTONOMY_COPY.killSwitchEngage}
        </button>
      </div>
      <div className="flex flex-col gap-1">
        <label
          htmlFor="kill-switch-reason"
          className="text-[10px] font-medium text-slate-500"
        >
          {AUTONOMY_COPY.killSwitchReasonLabel}
        </label>
        <textarea
          id="kill-switch-reason"
          data-testid="kill-switch-reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder={AUTONOMY_COPY.killSwitchReasonPlaceholder}
          rows={2}
          className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-700 placeholder:text-slate-400"
        />
      </div>
    </div>
  );
}
