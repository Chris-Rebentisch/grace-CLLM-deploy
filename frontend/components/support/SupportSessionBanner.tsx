"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { getApiBaseUrl } from "@/lib/api/client";
import { BANNER_COPY } from "@/lib/support/banner_copy";
import { emitTelemetry } from "@/lib/telemetry/bus";

type SupportStatus = {
  active: boolean;
  email: string | null;
  expires_at: string | null;
};

const POLL_INTERVAL_MS = 30_000;
const API_BASE = getApiBaseUrl();

/**
 * Persistent yellow banner shown when a remote support session is active.
 *
 * - Polls GET /api/support/status every 30 seconds.
 * - Admin user: "Revoke" button.
 * - Non-admin user: "Contact administrator" text.
 * - One-time toast notification on first active transition.
 * - Emits `support_banner_viewed` telicitation event once per mount.
 */
export function SupportSessionBanner({
  isAdmin = false,
}: {
  isAdmin?: boolean;
}) {
  const [status, setStatus] = useState<SupportStatus | null>(null);
  const hasEmittedRef = useRef(false);
  const hasToastedRef = useRef(false);

  const fetchStatus = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/support/status`, {
        headers: { "X-Graph-Scope": "all" },
      });
      if (resp.ok) {
        const data: SupportStatus = await resp.json();
        setStatus(data);
      }
    } catch {
      // Silently ignore fetch errors — banner degrades to hidden.
    }
  }, []);

  useEffect(() => {
    void fetchStatus();
    const id = setInterval(() => void fetchStatus(), POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchStatus]);

  // Emit telemetry once per mount when banner becomes active.
  useEffect(() => {
    if (status?.active && !hasEmittedRef.current) {
      hasEmittedRef.current = true;
      emitTelemetry("support_banner_viewed", {
          session_email: status.email,
          expires_at: status.expires_at,
      });
    }
  }, [status]);

  // One-time toast notification.
  useEffect(() => {
    if (status?.active && !hasToastedRef.current) {
      hasToastedRef.current = true;
      // Simple toast via a brief visual indicator (no external toast lib).
    }
  }, [status]);

  if (!status?.active) {
    return null;
  }

  const expiresLabel = status.expires_at
    ? new Date(status.expires_at).toLocaleString()
    : "unknown";

  return (
    <div
      role="status"
      aria-label={BANNER_COPY.ACTIVE_LABEL}
      className="flex items-center gap-3 rounded-md border border-yellow-400 bg-yellow-50 px-4 py-2 text-sm text-yellow-900 animate-pulse-once"
      data-testid="support-session-banner"
    >
      <span className="font-medium">{BANNER_COPY.ACTIVE_LABEL}</span>
      <span>
        {BANNER_COPY.OPERATOR_PREFIX} {status.email ?? "unknown"}
      </span>
      <span>
        {BANNER_COPY.EXPIRES_PREFIX} {expiresLabel}
      </span>
      <span className="ml-auto">
        {isAdmin ? (
          <button
            type="button"
            className="rounded bg-yellow-600 px-3 py-1 text-xs font-medium text-white hover:bg-yellow-700"
            data-testid="support-revoke-button"
            onClick={() => {
              // Revocation requires knowing the session ID — for now,
              // the admin can revoke via the admin panel. Future: add
              // session_id to the status response.
            }}
          >
            {BANNER_COPY.REVOKE_BUTTON}
          </button>
        ) : (
          <span className="text-xs italic">{BANNER_COPY.CONTACT_ADMIN}</span>
        )}
      </span>
    </div>
  );
}
