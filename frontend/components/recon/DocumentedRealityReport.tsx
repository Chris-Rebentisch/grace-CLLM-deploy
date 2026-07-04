"use client";

// Documented Reality Report viewer (Chunk 37, D286 / D288).
//
// Long-form descriptive prose (rendered from regeneration pipeline
// output) with a collapsible "Aggregation data" panel. Mount-time
// telemetry emit per D290.

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { emitTelemetry } from "@/lib/telemetry/bus";
import {
  DOCUMENTED_REALITY_AGGREGATIONS_HIDE,
  DOCUMENTED_REALITY_AGGREGATIONS_TOGGLE,
  DOCUMENTED_REALITY_BELOW_FLOOR_NOTICE,
  DOCUMENTED_REALITY_NARRATIVE_PLACEHOLDER,
  DOCUMENTED_REALITY_SUBTITLE,
  DOCUMENTED_REALITY_TITLE,
} from "@/lib/recon/report_copy";
import type { DocumentedRealityReportResponse } from "@/lib/api/recon-types";

export type DocumentedRealityReportProps = {
  data: DocumentedRealityReportResponse;
  reviewerHash?: string;
};

export function DocumentedRealityReport({
  data,
  reviewerHash,
}: DocumentedRealityReportProps) {
  const [showAggregations, setShowAggregations] = useState(false);

  useEffect(() => {
    emitTelemetry("documented_reality_report_viewed", {
      reviewer_hash: reviewerHash ?? "",
      report_id: data.report_id,
      viewed_at: new Date().toISOString(),
    });
  }, [data.report_id, reviewerHash]);

  return (
    <article
      data-testid="documented-reality-report"
      className="flex flex-col gap-4 p-4"
    >
      <header>
        <h2 className="text-lg font-semibold">{DOCUMENTED_REALITY_TITLE}</h2>
        <p className="text-sm text-slate-500">
          {DOCUMENTED_REALITY_SUBTITLE}
        </p>
      </header>

      {data.corpus_below_floor ? (
        <p
          data-testid="documented-reality-below-floor"
          className="rounded border border-sky-200 bg-sky-50 p-3 text-sm text-sky-900"
        >
          {DOCUMENTED_REALITY_BELOW_FLOOR_NOTICE}
        </p>
      ) : null}

      <section data-testid="documented-reality-narrative">
        {data.narrative ? (
          <p className="whitespace-pre-line text-sm leading-relaxed">
            {data.narrative}
          </p>
        ) : (
          <p className="text-sm italic text-slate-500">
            {DOCUMENTED_REALITY_NARRATIVE_PLACEHOLDER}
          </p>
        )}
      </section>

      <Button
        variant="outline"
        size="sm"
        onClick={() => setShowAggregations((s) => !s)}
        data-testid="documented-reality-aggregations-toggle"
      >
        {showAggregations
          ? DOCUMENTED_REALITY_AGGREGATIONS_HIDE
          : DOCUMENTED_REALITY_AGGREGATIONS_TOGGLE}
      </Button>

      {showAggregations ? (
        <section
          data-testid="documented-reality-aggregations"
          className="rounded border border-slate-200 p-3 text-xs"
        >
          <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1">
            <dt className="text-slate-500">Total vertices</dt>
            <dd>{data.aggregations.total_vertices}</dd>
            <dt className="text-slate-500">Total edges</dt>
            <dd>{data.aggregations.total_edges}</dd>
            <dt className="text-slate-500">Top entities</dt>
            <dd>
              {data.aggregations.top_entities.length === 0
                ? "—"
                : data.aggregations.top_entities
                    .map(
                      (e) =>
                        `${String(e.type_name ?? "?")}: ${String(e.count ?? 0)}`,
                    )
                    .join(", ")}
            </dd>
          </dl>
        </section>
      ) : null}
    </article>
  );
}
