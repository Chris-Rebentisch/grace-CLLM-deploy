"use client";
import { useSettingsStore } from "@/lib/state/settings-store";
import { emitTelemetry } from "@/lib/telemetry/bus";

// D232 design: the dialog's primary action IS the airgap_mode toggle.
// A single "Confirm" button is insufficient by design — the user must
// explicitly turn airgap off here.
export function AirgapBreakConfirmDialog() {
  const open = useSettingsStore((s) => s.airgapBreakDialogOpen);
  const close = useSettingsStore((s) => s.closeAirgapBreakDialog);
  const draft = useSettingsStore((s) => s.draft);
  const patch = useSettingsStore((s) => s.patchDraft);

  if (!open || !draft) return null;

  const flipAirgapOff = () => {
    patch({ airgap_mode: false });
    emitTelemetry("airgap_mode_toggled", { enabled: false });
    close();
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      data-testid="airgap-break-dialog"
      className="fixed inset-0 flex items-center justify-center bg-black/40"
    >
      <div className="w-96 rounded bg-white p-4 text-sm">
        <h3 className="text-base font-semibold">Disable airgap mode?</h3>
        <p className="mt-2 text-xs text-slate-700">
          This provider requires sending data over the internet. Disabling
          airgap mode is required to save this configuration.
        </p>
        <div className="mt-3 flex justify-end gap-2">
          <button
            type="button"
            onClick={close}
            data-testid="airgap-break-cancel"
            className="rounded border px-2 py-1 text-xs"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={flipAirgapOff}
            data-testid="airgap-break-disable"
            className="rounded bg-rose-700 px-2 py-1 text-xs text-white"
          >
            Disable airgap mode
          </button>
        </div>
      </div>
    </div>
  );
}
