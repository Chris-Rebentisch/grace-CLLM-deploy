"use client";
import { useSettingsStore } from "@/lib/state/settings-store";
import { useSessionStore } from "@/lib/state/session-store";
import { emitTelemetry } from "@/lib/telemetry/bus";

export function AirgapToggle() {
  const draft = useSettingsStore((s) => s.draft);
  const patch = useSettingsStore((s) => s.patchDraft);
  const sessionId = useSessionStore((s) => s.sessionId);

  if (!draft) return null;

  const flip = () => {
    const enabled = !draft.airgap_mode;
    patch({ airgap_mode: enabled });
    emitTelemetry("airgap_mode_toggled", { enabled });
    void sessionId;
  };

  return (
    <label
      data-testid="airgap-toggle"
      className="flex items-center gap-2 rounded border bg-white p-3 text-sm"
    >
      <input
        type="checkbox"
        data-testid="airgap-toggle-input"
        checked={draft.airgap_mode}
        onChange={flip}
      />
      Airgap mode (block cloud providers)
    </label>
  );
}
