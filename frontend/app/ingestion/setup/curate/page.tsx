"use client";

import { useEffect, useState, useCallback } from "react";
import { INGESTION_COPY } from "@/lib/ingestion/copy";
import { DiversityMetrics } from "@/components/ingestion/DiversityMetrics";
import { CurationEmailList } from "@/components/ingestion/CurationEmailList";
import { SampleSizeAdvisory } from "@/components/ingestion/SampleSizeAdvisory";
import { computeDiversityPreview } from "@/lib/ingestion/diversity-preview";
import { postElicitationEvent } from "@/lib/telemetry/emit";
import { buildEnvelope } from "@/lib/telemetry/events";
import { useSessionStore } from "@/lib/state/session-store";

interface EventItem {
  event_id: string;
  message_id: string;
  sender_email: string;
  sender_display_name: string | null;
  subject: string;
  sent_at: string | null;
  received_at: string | null;
  triage_tier_outcome: string;
}

interface CurationResult {
  subset_id: string;
  message_count: number;
  diversity_metrics: {
    sender_band: string;
    thread_depth_band: string;
    date_range_band: string;
  };
}

export default function CuratePage() {
  const [sourceId, setSourceId] = useState<string>("");
  const [deploymentPath, setDeploymentPath] = useState<string>("B");
  const [events, setEvents] = useState<EventItem[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [curationResult, setCurationResult] = useState<CurationResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchEvents = useCallback(async () => {
    if (!sourceId) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(
        `/api/ingestion/sources/${sourceId}/events?limit=100`,
        { headers: { "X-Graph-Scope": "all" } },
      );
      if (resp.ok) {
        const data = await resp.json();
        setEvents(data.items ?? []);
      } else {
        const data = await resp.json();
        setError(data.detail ?? "Failed to load events");
      }
    } catch {
      setError("Network error");
    } finally {
      setLoading(false);
    }
  }, [sourceId]);

  const handleToggle = (messageId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(messageId)) {
        next.delete(messageId);
      } else {
        next.add(messageId);
      }
      return next;
    });
  };

  const handleSelectAll = () => {
    if (selected.size === events.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(events.map((e) => e.message_id)));
    }
  };

  const selectedEvents = events.filter((e) => selected.has(e.message_id));
  const diversityPreview =
    selectedEvents.length > 0
      ? computeDiversityPreview(selectedEvents)
      : null;

  const handleCurate = async () => {
    if (selected.size === 0 || !sourceId) return;
    setError(null);
    try {
      const resp = await fetch("/api/ingestion/curate", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Graph-Scope": "all",
        },
        body: JSON.stringify({
          source_id: sourceId,
          selected_message_ids: Array.from(selected),
          deployment_path: deploymentPath,
        }),
      });
      if (resp.ok) {
        const data = await resp.json();
        setCurationResult(data);
        // Chunk 60, CP6: emit curation_submitted telemetry on success
        const sid = useSessionStore.getState().sessionId;
        if (sid) void postElicitationEvent(
          buildEnvelope({
            session_id: sid,
            phase_name: "none",
            event_type: "curation_submitted",
            payload: { source_id: sourceId, selected_count: selected.size },
          }),
        );
      } else {
        const data = await resp.json();
        setError(data.detail ?? "Curation failed");
      }
    } catch {
      setError("Network error");
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <div>
        <h1 className="text-xl font-semibold">
          {INGESTION_COPY.curatePageTitle}
        </h1>
        <p className="mt-1 text-sm text-gray-600">
          {INGESTION_COPY.curatePageDescription}
        </p>
      </div>

      <div className="flex gap-4">
        <div className="flex-1">
          <label className="block text-sm font-medium">
            {INGESTION_COPY.sourceIdLabel}
          </label>
          <input
            type="text"
            value={sourceId}
            onChange={(e) => setSourceId(e.target.value)}
            className="mt-1 w-full rounded border px-3 py-2 text-sm"
            placeholder="UUID"
          />
        </div>
        <div>
          <label className="block text-sm font-medium">
            {INGESTION_COPY.deploymentPathLabel}
          </label>
          <select
            value={deploymentPath}
            onChange={(e) => setDeploymentPath(e.target.value)}
            className="mt-1 rounded border px-3 py-2 text-sm"
          >
            <option value="B">Path B</option>
            <option value="C">Path C</option>
          </select>
        </div>
        <div className="flex items-end">
          <button
            onClick={fetchEvents}
            disabled={!sourceId || loading}
            className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {INGESTION_COPY.loadEventsButton}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {events.length > 0 && (
        <>
          <CurationEmailList
            events={events}
            selected={selected}
            onToggle={handleToggle}
            onSelectAll={handleSelectAll}
          />

          <SampleSizeAdvisory selectedCount={selected.size} />

          {diversityPreview && (
            <div
              className="rounded border border-gray-200 bg-gray-50 p-3 space-y-2"
              data-testid="diversity-preview"
            >
              <p className="text-sm font-medium">
                {INGESTION_COPY.diversityPreviewHeading}
              </p>
              <DiversityMetrics metrics={diversityPreview} />
              <p className="text-xs text-gray-600">
                {INGESTION_COPY.threadDepthV1Notice}
              </p>
            </div>
          )}

          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-600">
              {selected.size} {INGESTION_COPY.selectedCount}
            </span>
            <button
              onClick={handleCurate}
              disabled={selected.size === 0}
              className="rounded bg-green-600 px-4 py-2 text-sm text-white hover:bg-green-700 disabled:opacity-50"
            >
              {INGESTION_COPY.curateButton}
            </button>
          </div>
        </>
      )}

      {curationResult && (
        <div className="rounded border border-green-300 bg-green-50 p-4 space-y-3">
          <p className="text-sm font-medium">
            {INGESTION_COPY.curateSuccess} ({curationResult.message_count}{" "}
            {INGESTION_COPY.messagesLabel})
          </p>
          <DiversityMetrics metrics={curationResult.diversity_metrics} />
        </div>
      )}
    </div>
  );
}
