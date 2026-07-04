"use client";

/**
 * /sensitivity/report/[report_id] — single-report detail (Chunk 43, CP6).
 */

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { sensitivityApi } from "@/lib/api/sensitivity";
import { SensitivityClassificationReport } from "@/components/sensitivity/SensitivityClassificationReport";
import { SENSITIVITY_COPY } from "@/lib/sensitivity/copy";
import type { SensitivityClassificationReportResponse } from "@/lib/api/types";

export default function SensitivityReportPage({
  params,
}: {
  params: Promise<{ report_id: string }>;
}) {
  const { report_id } = use(params);
  const [report, setReport] =
    useState<SensitivityClassificationReportResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const out = await sensitivityApi.getReportById(report_id);
        if (!cancelled) setReport(out);
      } catch (e) {
        if (!cancelled)
          setErr(e instanceof Error ? e.message : "Failed to load report");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [report_id]);

  return (
    <main
      data-testid="sensitivity-report-detail-page"
      className="mx-auto flex max-w-4xl flex-col gap-3 p-4"
    >
      <header>
        <Link
          href="/sensitivity"
          className="text-[11px] text-slate-700 hover:underline"
        >
          ← {SENSITIVITY_COPY.pageTitle}
        </Link>
        <h1 className="mt-1 text-lg font-semibold text-slate-900">
          {SENSITIVITY_COPY.reportLatestHeading}
        </h1>
      </header>

      {loading ? (
        <p className="text-xs text-slate-500">Loading…</p>
      ) : err ? (
        <p
          data-testid="sensitivity-report-detail-error"
          className="rounded border border-rose-300 bg-rose-50 p-2 text-xs text-rose-700"
        >
          {err}
        </p>
      ) : report ? (
        <SensitivityClassificationReport report={report} />
      ) : (
        <p className="text-xs italic text-slate-500">
          {SENSITIVITY_COPY.reportNone}
        </p>
      )}
    </main>
  );
}
