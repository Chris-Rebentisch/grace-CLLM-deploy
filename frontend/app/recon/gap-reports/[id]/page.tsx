"use client";

/**
 * /recon/gap-reports/[id] — Gap report detail (Chunk 60, CP8).
 *
 * [id] = session_id (review session UUID, NOT gap_reports.id).
 * Mounts GapReportViewer with source-type filter extension.
 */

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { apiRequest } from "@/lib/api/client";
import { GapReportViewer } from "@/components/recon/GapReportViewer";
import type { GapReportResponse } from "@/lib/api/recon-types";

export default function GapReportDetailPage() {
  const params = useParams();
  const sessionId = params.id as string;
  const [data, setData] = useState<GapReportResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiRequest<GapReportResponse>(
        `/api/recon/gap-report/${sessionId}`,
      );
      setData(res);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (err) {
    return (
      <div className="p-4">
        <p className="text-red-700">{err}</p>
      </div>
    );
  }

  return (
    <div className="p-4" data-testid="gap-report-detail-page">
      <GapReportViewer data={data} />
    </div>
  );
}
