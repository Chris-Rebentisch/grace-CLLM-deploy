"use client";

// Gap Report viewer (Chunk 36 D278 / Chunk 37 D288 frontend completion).
//
// Three sections per the existing `GapReportResponse` shape. Mount-time
// `gap_report_viewed` emit closes Chunk 36 Deviation #3 frontend side.

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { CoveringDirectivesBanner } from "@/components/recon/CoveringDirectivesBanner";
import { emitTelemetry } from "@/lib/telemetry/bus";
import {
  GAP_REPORT_EMPTY,
  GAP_REPORT_SECTION_LABELS,
  GAP_REPORT_SUBTITLE,
  GAP_REPORT_TITLE,
} from "@/lib/recon/report_copy";
import type { GapReportResponse } from "@/lib/api/recon-types";

export type GapReportViewerProps = {
  data: GapReportResponse | null;
  reviewerHash?: string;
};

export function GapReportViewer({ data, reviewerHash }: GapReportViewerProps) {
  const [sourceTypeFilter, setSourceTypeFilter] = useState<string>("all");

  useEffect(() => {
    if (data) {
      emitTelemetry("gap_report_viewed", {
        reviewer_hash: reviewerHash ?? "",
        session_id: data.session_id,
        viewed_at: new Date().toISOString(),
      });
    }
  }, [data, reviewerHash]);

  if (!data) {
    return (
      <p
        data-testid="gap-report-empty"
        className="rounded border border-dashed border-slate-300 p-4 text-sm text-slate-500"
      >
        {GAP_REPORT_EMPTY}
      </p>
    );
  }

  // Source-type breakdown stacked bar (Chunk 60, CP8)
  const breakdown = (data as Record<string, unknown>).report_json
    ? ((data as Record<string, unknown>).report_json as Record<string, unknown>)
        .source_type_breakdown as Record<string, number> | undefined
    : undefined;

  return (
    <article
      data-testid="gap-report-viewer"
      className="flex flex-col gap-4 p-4"
    >
      <header>
        <h2 className="text-lg font-semibold">{GAP_REPORT_TITLE}</h2>
        <p className="text-sm text-slate-500">{GAP_REPORT_SUBTITLE}</p>
      </header>

      {/* Source-type filter (Chunk 60, CP8) */}
      <div className="flex gap-1" data-testid="source-type-filter">
        {["all", "document", "communication", "mixed"].map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => {
              setSourceTypeFilter(t);
              emitTelemetry("recon_source_filter_applied", {
                filter_type: "source_type",
                filter_value: t,
              });
            }}
            className={`rounded border px-2 py-0.5 text-xs ${
              sourceTypeFilter === t
                ? "border-blue-400 bg-blue-50 text-blue-800"
                : "border-slate-300 text-slate-600"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Source-type breakdown bar (Chunk 60, CP8) */}
      {breakdown && (
        <div className="flex h-4 overflow-hidden rounded" data-testid="source-type-breakdown">
          {Object.entries(breakdown).map(([key, count]) => {
            const total = Object.values(breakdown).reduce((a, b) => a + b, 0);
            const pct = total > 0 ? (count / total) * 100 : 0;
            return (
              <div
                key={key}
                className={`${
                  key === "document"
                    ? "bg-slate-400"
                    : key === "communication"
                      ? "bg-slate-600"
                      : "bg-slate-500"
                }`}
                style={{ width: `${pct}%` }}
                title={`${key}: ${count}`}
              />
            );
          })}
        </div>
      )}

      <CoveringDirectivesBanner directives={data.covering_directives ?? []} />

      <Section
        testId="gap-report-emphasized-with-evidence"
        label={GAP_REPORT_SECTION_LABELS.emphasized_with_evidence}
      >
        {data.emphasized_with_evidence.length === 0 ? (
          <p className="text-xs text-slate-400">—</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {data.emphasized_with_evidence.map((item) => (
              <li
                key={`${item.element_type}:${item.element_name}`}
                className="flex items-center justify-between gap-2 rounded border border-slate-200 px-2 py-1"
              >
                <span className="text-sm">
                  {item.element_name}{" "}
                  <span className="text-slate-500">
                    ({item.element_type})
                  </span>
                </span>
                <Badge variant="secondary">
                  Evidence: {item.instance_count}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section
        testId="gap-report-emphasized-without-evidence"
        label={GAP_REPORT_SECTION_LABELS.emphasized_without_evidence}
      >
        {data.emphasized_without_evidence.length === 0 ? (
          <p className="text-xs text-slate-400">—</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {data.emphasized_without_evidence.map((item) => (
              <li
                key={`${item.element_type}:${item.element_name}`}
                className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-sm"
              >
                {item.element_name}{" "}
                <span className="text-slate-500">({item.element_type})</span>
                {item.suggested_actions.length > 0 ? (
                  <ul className="mt-1 list-disc pl-5 text-xs text-slate-600">
                    {item.suggested_actions.map((a) => (
                      <li key={a}>{a}</li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section
        testId="gap-report-unemphasized-in-evidence"
        label={GAP_REPORT_SECTION_LABELS.unemphasized_in_evidence}
      >
        {data.unemphasized_in_evidence.length === 0 ? (
          <p className="text-xs text-slate-400">—</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {data.unemphasized_in_evidence.map((item) => (
              <li
                key={`${item.element_type}:${item.element_name}`}
                className="rounded border border-slate-200 px-2 py-1 text-sm"
              >
                {item.element_name}{" "}
                <span className="text-slate-500">({item.element_type})</span>
                {" — "}
                <span className="text-xs text-slate-500">
                  {item.decision_status}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </article>
  );
}

function Section({
  testId,
  label,
  children,
}: {
  testId: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <section data-testid={testId} className="flex flex-col gap-2">
      <h3 className="text-sm font-medium">{label}</h3>
      {children}
    </section>
  );
}
