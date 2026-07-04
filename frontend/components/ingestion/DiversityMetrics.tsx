"use client";

import { INGESTION_COPY } from "@/lib/ingestion/copy";

interface DiversityMetricsProps {
  metrics: {
    sender_band: string;
    thread_depth_band: string;
    date_range_band: string;
  };
}

export function DiversityMetrics({ metrics }: DiversityMetricsProps) {
  return (
    <div className="space-y-1 text-sm">
      <div className="flex gap-2">
        <span className="font-medium">{INGESTION_COPY.senderDiversityLabel}:</span>
        <span>{metrics.sender_band}</span>
      </div>
      <div className="flex gap-2">
        <span className="font-medium">{INGESTION_COPY.threadDepthLabel}:</span>
        <span>{metrics.thread_depth_band}</span>
      </div>
      <div className="flex gap-2">
        <span className="font-medium">{INGESTION_COPY.dateRangeLabel}:</span>
        <span>{metrics.date_range_band}</span>
      </div>
    </div>
  );
}
