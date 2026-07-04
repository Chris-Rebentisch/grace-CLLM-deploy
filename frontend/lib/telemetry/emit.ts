"use client";

import { apiClient } from "@/lib/api/client";
import type {
  ElicitationEventAck,
  ElicitationEventEnvelope,
} from "@/lib/api/types";

// Backend ingest. 422 responses mean the envelope is malformed client
// side — log and drop (per spec §7.2). No retries; the local telemetry
// bus already preserved the event for CI.
export async function postElicitationEvent(
  envelope: ElicitationEventEnvelope,
): Promise<ElicitationEventAck | null> {
  try {
    return await apiClient.post<ElicitationEventAck>(
      "/api/elicitation/events",
      envelope,
    );
  } catch (err) {
    if (typeof console !== "undefined") {
      // Keep console.warn, not console.error — a telemetry drop is not
      // a functional failure in v1.
      console.warn("[telemetry] emit failed", err);
    }
    return null;
  }
}
