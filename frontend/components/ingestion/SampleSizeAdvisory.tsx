"use client";

import { INGESTION_COPY } from "@/lib/ingestion/copy";
import { getSampleSizeGuidance } from "@/lib/ingestion/sample-size";

interface SampleSizeAdvisoryProps {
  selectedCount: number;
}

export function SampleSizeAdvisory({ selectedCount }: SampleSizeAdvisoryProps) {
  if (selectedCount === 0) return null;

  const kind = getSampleSizeGuidance(selectedCount);

  if (kind === "neutral") return null;

  const className =
    kind === "warning_low"
      ? "rounded border border-amber-300 bg-amber-50 p-2 text-sm text-amber-800"
      : kind === "representative"
        ? "rounded border border-green-300 bg-green-50 p-2 text-sm text-green-800"
        : "rounded border border-yellow-300 bg-yellow-50 p-2 text-sm text-yellow-800";

  const message =
    kind === "warning_low"
      ? INGESTION_COPY.sampleSizeWarningLow
      : kind === "representative"
        ? INGESTION_COPY.sampleSizeRepresentative
        : INGESTION_COPY.sampleSizeWarningHigh;

  return (
    <div className={className} data-testid="sample-size-advisory">
      {message}
    </div>
  );
}

