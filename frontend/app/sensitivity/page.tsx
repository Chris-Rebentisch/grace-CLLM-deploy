"use client";

/**
 * /sensitivity — Sensitivity Gate landing (Chunk 43, CP6).
 *
 * Shows the active matrix tagged-subset preview, generation CTA, and
 * the latest classification report card. The Sensitivity Gate is a
 * render surface over the Chunk 42 Permission Matrix engine (D270).
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { permissionsApi } from "@/lib/api/permissions";
import { sensitivityApi } from "@/lib/api/sensitivity";
import { SENSITIVITY_COPY } from "@/lib/sensitivity/copy";
import { SensitivityClassificationReport } from "@/components/sensitivity/SensitivityClassificationReport";
import { SensitivityCoverageBadge } from "@/components/sensitivity/SensitivityCoverageBadge";
import { SensitivityReportRatifyDialog } from "@/components/sensitivity/SensitivityReportRatifyDialog";
import { TaggedSubsetTable } from "@/components/sensitivity/TaggedSubsetTable";
import type {
  PermissionMatrixVersion,
  SensitivityClassificationReportResponse,
  TaggedSubset,
} from "@/lib/api/types";

export default function SensitivityPage() {
  const [activeMatrix, setActiveMatrix] =
    useState<PermissionMatrixVersion | null>(null);
  const [latestReport, setLatestReport] =
    useState<SensitivityClassificationReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [m, r] = await Promise.all([
          permissionsApi.getActiveMatrix().catch(() => null),
          sensitivityApi.getLatestReport().catch(() => null),
        ]);
        if (cancelled) return;
        setActiveMatrix(m);
        setLatestReport(r);
      } catch (e) {
        if (!cancelled)
          setErr(e instanceof Error ? e.message : "Failed to load sensitivity surface");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const taggedSubset: TaggedSubset | null = useMemo(() => {
    if (!activeMatrix) return null;
    const matrixPayload = (activeMatrix as unknown as {
      payload?: Record<string, unknown>;
    }).payload;
    if (!matrixPayload) return null;
    return sensitivityApi.projectTaggedSubset(
      matrixPayload as { schema_version?: string; role_clusters?: Array<Record<string, unknown>> },
    );
  }, [activeMatrix]);

  return (
    <main
      data-testid="sensitivity-page"
      className="mx-auto flex max-w-4xl flex-col gap-4 p-4"
    >
      <header className="flex items-center justify-between gap-2">
        <h1 className="text-lg font-semibold text-slate-900">
          {SENSITIVITY_COPY.pageTitle}
        </h1>
        <button
          type="button"
          data-testid="sensitivity-generate-report-cta"
          onClick={() => setDialogOpen(true)}
          className="rounded border border-slate-700 bg-slate-800 px-3 py-1 text-xs font-medium text-white"
        >
          {latestReport
            ? SENSITIVITY_COPY.reportRegenerateCta
            : SENSITIVITY_COPY.reportGenerateCta}
        </button>
      </header>

      <nav className="flex gap-3 text-[11px]">
        <Link
          href="/sensitivity/audit-trail"
          data-testid="sensitivity-audit-trail-link"
          className="text-slate-700 hover:underline"
        >
          {SENSITIVITY_COPY.auditTrailHeading} →
        </Link>
      </nav>

      {err ? (
        <p
          data-testid="sensitivity-page-error"
          className="rounded border border-rose-300 bg-rose-50 p-2 text-xs text-rose-700"
        >
          {err}
        </p>
      ) : null}

      <section
        data-testid="sensitivity-tagged-subset-section"
        className="rounded-md border border-slate-200 bg-white p-3"
      >
        <h2 className="mb-2 text-sm font-semibold text-slate-900">
          {SENSITIVITY_COPY.taggedSubsetHeading}
        </h2>
        {loading ? (
          <p className="text-xs text-slate-500">Loading…</p>
        ) : taggedSubset ? (
          <TaggedSubsetTable subset={taggedSubset} />
        ) : (
          <p
            data-testid="sensitivity-no-active-matrix"
            className="text-xs italic text-slate-500"
          >
            {SENSITIVITY_COPY.taggedSubsetEmpty}
          </p>
        )}
      </section>

      <section
        data-testid="sensitivity-latest-report-section"
        className="rounded-md border border-slate-200 bg-white p-3"
      >
        <div className="mb-2 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-slate-900">
            {SENSITIVITY_COPY.reportLatestHeading}
          </h2>
          {latestReport ? (
            <SensitivityCoverageBadge
              band={latestReport.coverage_band}
              testId="sensitivity-latest-coverage-badge"
            />
          ) : null}
        </div>
        {loading ? (
          <p className="text-xs text-slate-500">Loading…</p>
        ) : latestReport ? (
          <div className="flex flex-col gap-2">
            <Link
              href={`/sensitivity/report/${encodeURIComponent(latestReport.report_id)}`}
              data-testid="sensitivity-latest-report-link"
              className="text-[11px] text-slate-700 hover:underline"
            >
              View full report →
            </Link>
            <SensitivityClassificationReport report={latestReport} />
          </div>
        ) : (
          <p
            data-testid="sensitivity-no-report"
            className="text-xs italic text-slate-500"
          >
            {SENSITIVITY_COPY.reportNone}
          </p>
        )}
      </section>

      <SensitivityReportRatifyDialog
        open={dialogOpen}
        force={latestReport !== null}
        onClose={() => setDialogOpen(false)}
        onGenerated={(report) => setLatestReport(report)}
      />
    </main>
  );
}
