"use client";

/**
 * /recon/documented-reality/[id] — DR report detail (Chunk 60, CP8).
 *
 * [id] = report_id.
 * Mounts DocumentedRealityReport with evidence-origin generation-time
 * operator action (origin chooser + "Generate narrative" button).
 */

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { apiRequest } from "@/lib/api/client";
import { DocumentedRealityReport } from "@/components/recon/DocumentedRealityReport";
import { postElicitationEvent } from "@/lib/telemetry/emit";
import { buildEnvelope } from "@/lib/telemetry/events";
import { useSessionStore } from "@/lib/state/session-store";
import type { DocumentedRealityReportResponse } from "@/lib/api/recon-types";

type DRReportResponse = DocumentedRealityReportResponse;

export default function DocumentedRealityDetailPage() {
  const params = useParams();
  const reportId = params.id as string;
  const sessionId = useSessionStore((s) => s.sessionId);
  const [data, setData] = useState<DRReportResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [evidenceOrigin, setEvidenceOrigin] = useState<string>("both");
  const [generating, setGenerating] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await apiRequest<DRReportResponse>(
        `/api/recon/documented-reality/${reportId}`,
      );
      setData(res);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    }
  }, [reportId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      const params = new URLSearchParams();
      if (evidenceOrigin !== "both") {
        params.set("evidence_origin", evidenceOrigin);
      }
      await apiRequest(
        `/api/recon/documented-reality/generate${params.toString() ? `?${params}` : ""}`,
        { method: "POST", body: {} },
      );
      // Re-fetch to show updated narrative
      await load();

      if (sessionId) void postElicitationEvent(
        buildEnvelope({
          session_id: sessionId,
          phase_name: "none",
          event_type: "recon_source_filter_applied",
          payload: {
            filter_type: "evidence_origin",
            filter_value: evidenceOrigin,
          },
        }),
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : "generate failed");
    } finally {
      setGenerating(false);
    }
  };

  if (err) {
    return (
      <div className="p-4">
        <p className="text-red-700">{err}</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="p-4">
        <p className="text-slate-400">Loading...</p>
      </div>
    );
  }

  return (
    <div className="p-4" data-testid="dr-detail-page">
      <DocumentedRealityReport data={data} />

      {/* Evidence-origin generation-time operator action (Chunk 60, CP8) */}
      <div
        className="mt-4 rounded border border-slate-200 p-3"
        data-testid="evidence-origin-action"
      >
        <h3 className="mb-2 text-sm font-medium text-slate-600">
          Generate narrative by evidence origin
        </h3>
        <div className="flex items-center gap-2">
          <select
            value={evidenceOrigin}
            onChange={(e) => setEvidenceOrigin(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            data-testid="evidence-origin-select"
          >
            <option value="both">Both (document + communication)</option>
            <option value="document">Document only</option>
            <option value="communication">Communication only</option>
          </select>
          <button
            type="button"
            onClick={() => void handleGenerate()}
            disabled={generating}
            className="rounded border border-blue-400 bg-blue-50 px-3 py-1 text-sm font-medium text-blue-800 hover:bg-blue-100 disabled:opacity-50"
            data-testid="generate-narrative-btn"
          >
            {generating ? "Generating..." : "Generate narrative"}
          </button>
        </div>
      </div>
    </div>
  );
}
