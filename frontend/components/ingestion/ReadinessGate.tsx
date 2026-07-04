"use client";

import { useEffect, useState } from "react";
import { INGESTION_COPY } from "@/lib/ingestion/copy";

interface SegmentReadiness {
  segment: string;
  ready: boolean;
  person_count: number;
  organization_count: number;
  accepted_cq_count: number;
  guidance: string;
}

interface ReadinessResult {
  deployment_path: string;
  segments: SegmentReadiness[];
  overall_ready: boolean;
  bootstrap_pending: boolean;
  thresholds: { cq_mention_threshold: number; confidence_threshold: number };
}

export function ReadinessGate() {
  const [result, setResult] = useState<ReadinessResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchReadiness = async () => {
      try {
        const resp = await fetch("/api/ingestion/readiness", {
          headers: { "X-Graph-Scope": "all" },
        });
        if (resp.ok) {
          setResult(await resp.json());
        } else {
          const data = await resp.json();
          setError(data.detail ?? "Failed to load readiness");
        }
      } catch {
        setError("Network error");
      }
    };

    fetchReadiness();
  }, []);

  if (error) {
    return <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm">{error}</div>;
  }

  if (!result) {
    return <div className="text-sm text-gray-500">Loading readiness...</div>;
  }

  if (result.bootstrap_pending) {
    return (
      <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm">
        {INGESTION_COPY.readinessBootstrapPending}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium">{INGESTION_COPY.readinessHeading}</h3>
      <div
        className={`rounded border p-3 text-sm ${
          result.overall_ready ? "border-green-300 bg-green-50" : "border-amber-300 bg-amber-50"
        }`}
      >
        <p className="font-medium">
          {result.overall_ready ? INGESTION_COPY.readinessReady : INGESTION_COPY.readinessNotReady}
        </p>
      </div>
      {result.segments.length > 0 && (
        <div className="space-y-2">
          {result.segments.map((seg) => (
            <div
              key={seg.segment}
              className={`rounded border p-2 text-sm ${
                seg.ready ? "border-green-200" : "border-red-200"
              }`}
            >
              <p className="font-medium">{seg.segment}</p>
              <div className="mt-1 flex gap-4 text-xs text-gray-600">
                <span>{INGESTION_COPY.personCountLabel}: {seg.person_count}</span>
                <span>{INGESTION_COPY.orgCountLabel}: {seg.organization_count}</span>
                <span>{INGESTION_COPY.cqCountLabel}: {seg.accepted_cq_count}</span>
              </div>
              {seg.guidance && <p className="mt-1 text-xs text-amber-600">{seg.guidance}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
