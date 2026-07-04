"use client";

/**
 * /recon/divergence-maps/[id] — Divergence map detail (Chunk 60, CP8).
 *
 * [id] = map_id.
 * Mounts DivergenceMap with source-origins badge extension.
 */

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { apiRequest } from "@/lib/api/client";
import { DivergenceMap } from "@/components/recon/DivergenceMap";
import type { DivergenceMapResponse } from "@/lib/api/recon-types";

export default function DivergenceMapDetailPage() {
  const params = useParams();
  const mapId = params.id as string;
  const [data, setData] = useState<DivergenceMapResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiRequest<DivergenceMapResponse>(
        `/api/recon/divergence-map/${mapId}`,
      );
      setData(res);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    }
  }, [mapId]);

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

  if (!data) {
    return (
      <div className="p-4">
        <p className="text-slate-400">Loading...</p>
      </div>
    );
  }

  return (
    <div className="p-4" data-testid="divergence-map-detail-page">
      <DivergenceMap data={data} />
    </div>
  );
}
