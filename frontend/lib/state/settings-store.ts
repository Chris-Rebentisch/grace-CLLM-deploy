"use client";
import { create } from "zustand";

// In-flight settings draft that the Settings panel mutates before the user
// commits a Save. Kept separate from the server snapshot in TanStack Query
// so the Save button can compute "dirty" and the AirgapBreakConfirmDialog
// can swap the draft's airgap_mode without touching the persisted value.
export type SettingsDraft = {
  provider: string;
  model: string;
  base_url: string;
  timeout: number;
  api_key: string; // local-only; "" means do-not-change.
  airgap_mode: boolean;
};

type SettingsState = {
  draft: SettingsDraft | null;
  baseSnapshot: SettingsDraft | null;
  airgapBreakDialogOpen: boolean;
};

type SettingsActions = {
  initDraft: (draft: SettingsDraft) => void;
  patchDraft: (patch: Partial<SettingsDraft>) => void;
  resetDraft: () => void;
  openAirgapBreakDialog: () => void;
  closeAirgapBreakDialog: () => void;
  isDirty: () => boolean;
};

export type SettingsStore = SettingsState & SettingsActions;

export const useSettingsStore = create<SettingsStore>((set, get) => ({
  draft: null,
  baseSnapshot: null,
  airgapBreakDialogOpen: false,

  initDraft: (draft) => set({ draft, baseSnapshot: { ...draft } }),
  patchDraft: (patch) =>
    set((s) => (s.draft ? { draft: { ...s.draft, ...patch } } : {})),
  resetDraft: () =>
    set((s) => (s.baseSnapshot ? { draft: { ...s.baseSnapshot } } : {})),
  openAirgapBreakDialog: () => set({ airgapBreakDialogOpen: true }),
  closeAirgapBreakDialog: () => set({ airgapBreakDialogOpen: false }),
  isDirty: () => {
    const { draft, baseSnapshot } = get();
    if (!draft || !baseSnapshot) return false;
    return (
      draft.provider !== baseSnapshot.provider ||
      draft.model !== baseSnapshot.model ||
      draft.base_url !== baseSnapshot.base_url ||
      draft.timeout !== baseSnapshot.timeout ||
      draft.api_key !== "" ||
      draft.airgap_mode !== baseSnapshot.airgap_mode
    );
  },
}));
