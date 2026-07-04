"use client";

/**
 * /ingestion/[source_id] — Per-source detail page (Chunk 60, CP4).
 *
 * Deep-linkable. Shows source status, triage breakdown, event list,
 * and re-consent CTA for OAuth sources in error status.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  fetchIngestionSource,
  fetchSourceStatus,
  fetchSourceEvents,
  fetchOAuthInit,
  type IngestionSourceItem,
  type SourceStatusResponse,
  type IngestionEventItem,
} from "@/lib/api/ingestion";
import { postElicitationEvent } from "@/lib/telemetry/emit";
import { buildEnvelope } from "@/lib/telemetry/events";
import { useSessionStore } from "@/lib/state/session-store";

const EVENTS_PER_PAGE = 50;

const OAUTH_TYPES = new Set(["exchange", "gmail", "imap"]);

export default function SourceDetailPage() {
  const params = useParams();
  const sourceId = params.source_id as string;
  const sessionId = useSessionStore((s) => s.sessionId);
  const telemetrySent = useRef(false);

  const [source, setSource] = useState<IngestionSourceItem | null>(null);
  const [sourceStatus, setSourceStatus] = useState<SourceStatusResponse | null>(null);
  const [events, setEvents] = useState<IngestionEventItem[]>([]);
  const [eventsCursor, setEventsCursor] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const loadSource = useCallback(async () => {
    try {
      const [src, status] = await Promise.all([
        fetchIngestionSource(sourceId),
        fetchSourceStatus(sourceId),
      ]);
      setSource(src);
      setSourceStatus(status);
    } catch (e: unknown) {
      if (e && typeof e === "object" && "status" in e && (e as { status: number }).status === 404) {
        setNotFound(true);
      } else {
        setErr(e instanceof Error ? e.message : "load failed");
      }
    }
  }, [sourceId]);

  const loadEvents = useCallback(
    async (cursor?: string) => {
      try {
        const res = await fetchSourceEvents(sourceId, cursor, EVENTS_PER_PAGE);
        if (cursor) {
          setEvents((prev) => [...prev, ...res.items]);
        } else {
          setEvents(res.items);
        }
        setEventsCursor(res.next_cursor);
      } catch {
        // Events load failure is non-fatal
      }
    },
    [sourceId],
  );

  useEffect(() => {
    void loadSource();
    void loadEvents();
  }, [loadSource, loadEvents]);

  // Telemetry on mount
  useEffect(() => {
    if (telemetrySent.current || !sessionId) return;
    telemetrySent.current = true;
    void postElicitationEvent(
      buildEnvelope({
        session_id: sessionId,
        phase_name: "none",
        event_type: "ingestion_source_detail_viewed",
        payload: { source_id: sourceId },
      }),
    );
  }, [sourceId, sessionId]);

  if (notFound) {
    return (
      <div className="p-4" data-testid="source-detail-404">
        <h1 className="text-lg font-semibold">Source not found</h1>
        <p className="text-slate-500">
          <Link href="/ingestion" className="text-blue-600 underline">
            Back to dashboard
          </Link>
        </p>
      </div>
    );
  }

  if (err) {
    return (
      <div className="p-4">
        <p className="text-red-700">{err}</p>
      </div>
    );
  }

  if (!source) {
    return (
      <div className="p-4">
        <p className="text-slate-400">Loading...</p>
      </div>
    );
  }

  const isOAuth = OAUTH_TYPES.has(source.source_type);
  const showReconsent = source.status === "error" && isOAuth;

  return (
    <div className="p-4" data-testid="source-detail">
      <Link
        href="/ingestion"
        className="mb-2 inline-block text-xs text-blue-600 underline"
      >
        &larr; Dashboard
      </Link>

      <h1 className="mb-1 text-lg font-semibold">{source.name}</h1>
      <p className="mb-4 text-sm text-slate-500">
        Type: {source.source_type} &mdash; Status:{" "}
        <span data-testid="source-status">{source.status}</span>
      </p>

      {showReconsent && (
        <div
          className="mb-4 rounded border border-rose-300 bg-rose-50 px-4 py-2 text-sm text-rose-800"
          data-testid="reconsent-cta"
        >
          OAuth credentials expired or adapter error.{" "}
          <button
            type="button"
            className="font-medium underline"
            onClick={() => void fetchOAuthInit(source.source_type)}
          >
            Re-authorize
          </button>
        </div>
      )}

      {source.status === "error" && !isOAuth && (
        <div className="mb-4 rounded border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-800">
          Check configuration for this source.
        </div>
      )}

      {/* Triage breakdown */}
      {sourceStatus && (
        <section className="mb-4">
          <h2 className="mb-1 text-sm font-medium text-slate-600">
            Source status
          </h2>
          <p className="text-sm" data-testid="triage-breakdown">
            Last run: {sourceStatus.last_run_at ?? "never"}
            {sourceStatus.error_message && (
              <span className="ml-2 text-rose-600">
                {sourceStatus.error_message}
              </span>
            )}
          </p>
        </section>
      )}

      {/* Event list */}
      <section>
        <h2 className="mb-2 text-sm font-medium text-slate-600">Events</h2>
        {events.length === 0 ? (
          <p className="text-sm text-slate-400">No events yet.</p>
        ) : (
          <>
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b text-slate-500">
                  <th className="py-1">Sender</th>
                  <th className="py-1">Subject</th>
                  <th className="py-1">Sent</th>
                  <th className="py-1">Triage</th>
                </tr>
              </thead>
              <tbody>
                {events.map((ev) => (
                  <tr
                    key={ev.event_id}
                    className="border-b border-slate-100"
                    data-testid="event-row"
                  >
                    <td className="py-1">{ev.sender_email ?? "—"}</td>
                    <td className="py-1">{ev.subject ?? "—"}</td>
                    <td className="py-1">{ev.sent_at ?? "—"}</td>
                    <td className="py-1">
                      <span className="rounded bg-slate-100 px-1 py-0.5">
                        {ev.triage_tier_outcome ?? "pending"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {eventsCursor && (
              <button
                type="button"
                className="mt-2 text-xs text-blue-600 underline"
                onClick={() => void loadEvents(eventsCursor)}
                data-testid="load-more-events"
              >
                Load more
              </button>
            )}
          </>
        )}
      </section>
    </div>
  );
}
