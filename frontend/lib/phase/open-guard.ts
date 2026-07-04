"use client";

import { emitTelemetry } from "@/lib/telemetry/bus";
import { useSessionStore } from "@/lib/state/session-store";

// D196 classification. The guard treats any event whose mount type is in
// the forbidden list as an EC-4 violation during the Open phase. Passive
// render updates (positive list) are always allowed.

export type GuardMountType =
  | "modal"
  | "toast"
  | "snackbar"
  | "alert"
  | "browser_prompt"
  | "system_redirect"
  | "background_refetch_jump"
  | "focus_steal"
  | "layout_shift_mount"
  | "response_paint"
  | "a11y_announcement"
  | "react_reconciliation_remount"
  | "latency_milestone_update"
  | "scroll_anchor";

const FORBIDDEN_MOUNT_TYPES: ReadonlySet<GuardMountType> = new Set([
  "modal",
  "toast",
  "snackbar",
  "alert",
  "browser_prompt",
  "system_redirect",
  "background_refetch_jump",
  "focus_steal",
  "layout_shift_mount",
]);

const POSITIVE_MOUNT_TYPES: ReadonlySet<GuardMountType> = new Set([
  "response_paint",
  "a11y_announcement",
  "react_reconciliation_remount",
  "latency_milestone_update",
  "scroll_anchor",
]);

export type GuardCheckResult = {
  allowed: boolean;
  reason: "open_phase_forbidden" | "positive_list" | "not_open_phase" | "unknown_mount";
};

export function isOpenPhaseActive(): boolean {
  const state = useSessionStore.getState();
  return state.activePhase === "open" && state.sessionStatus === "active";
}

export function checkMount(
  mountType: GuardMountType,
  details?: Record<string, unknown>,
): GuardCheckResult {
  if (!isOpenPhaseActive()) {
    return { allowed: true, reason: "not_open_phase" };
  }
  if (POSITIVE_MOUNT_TYPES.has(mountType)) {
    return { allowed: true, reason: "positive_list" };
  }
  if (FORBIDDEN_MOUNT_TYPES.has(mountType)) {
    emitTelemetry("protocol_violation_detected", {
      violation_type: "ec4_attention_stealing_mount",
      details: { mount_type: mountType, ...(details ?? {}) },
    });
    return { allowed: false, reason: "open_phase_forbidden" };
  }
  // Unknown mount types default to allowed but emit an advisory. This
  // prevents false positives from new UI additions while keeping a trail
  // for the reviewer to audit.
  emitTelemetry("protocol_violation_detected", {
    violation_type: "ec4_unknown_mount",
    details: { mount_type: mountType, ...(details ?? {}) },
  });
  return { allowed: false, reason: "unknown_mount" };
}

// Convenience helpers for common call sites. Components should prefer
// these and only fall back to checkMount() with an explicit type string
// when a richer classification is needed.
export function guardToast(details?: Record<string, unknown>) {
  return checkMount("toast", details);
}

export function guardModal(details?: Record<string, unknown>) {
  return checkMount("modal", details);
}

export function guardRefetchJump(details?: Record<string, unknown>) {
  return checkMount("background_refetch_jump", details);
}

export function guardPassive(type: Extract<GuardMountType, "response_paint" | "a11y_announcement" | "react_reconciliation_remount" | "latency_milestone_update" | "scroll_anchor">) {
  return checkMount(type);
}

export const __internal = {
  FORBIDDEN_MOUNT_TYPES,
  POSITIVE_MOUNT_TYPES,
};
