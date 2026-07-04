"use client";
import Link from "next/link";
import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useProcessingStatus } from "@/lib/query/sources";
import { apiClient } from "@/lib/api/client";
import { CQGenerationPanel } from "@/components/onboarding/CQGenerationPanel";
import { OntologyProposalPanel } from "@/components/onboarding/OntologyProposalPanel";

export default function OnboardingPage() {
  const { data: proc } = useProcessingStatus(true);
  const docCount = proc?.by_status
    ? Object.values(proc.by_status).reduce((a, b) => a + b, 0)
    : 0;

  const cqSummary = useQuery({
    queryKey: ["cq-summary"],
    queryFn: () => apiClient.getCqSummary(),
    refetchOnWindowFocus: false,
  });

  // Latest completed merge → canonical review-set size. This is the number the
  // operator cares about (50), not the raw generated row count (220).
  const mergeLatest = useQuery({
    queryKey: ["cq-merge-latest"],
    queryFn: () => apiClient.getLatestCqMerge(),
    refetchOnWindowFocus: false,
  });

  const [cqCount, setCqCount] = useState(0);
  useEffect(() => {
    if (cqSummary.data) setCqCount(cqSummary.data.total ?? 0);
  }, [cqSummary.data]);

  const canonicalCount =
    mergeLatest.data?.has_merge && mergeLatest.data.canonical_count != null
      ? mergeLatest.data.canonical_count
      : null;

  return (
    <div className="space-y-4 p-4" data-testid="onboarding-page">
      <div>
        <h1 className="text-lg font-semibold">Build the knowledge graph</h1>
        <p className="text-xs text-slate-500">
          Sources &amp; processing (
          <Link href="/sources" className="underline">
            Sources
          </Link>
          ) → generate competency questions → propose &amp; review the ontology →
          approve → extract into the graph.
        </p>
      </div>

      <div className="rounded border bg-slate-50 p-3 text-xs">
        <span className="font-medium">Processed documents:</span>{" "}
        <span data-testid="onboarding-doc-count">{docCount}</span>
        <span className="mx-2 text-slate-300">|</span>
        <span className="font-medium">Competency questions:</span>{" "}
        {canonicalCount !== null ? (
          <>
            <span data-testid="onboarding-cq-count">{canonicalCount}</span>
            <span className="text-slate-500"> canonical</span>
            {cqCount > canonicalCount && (
              <span className="text-slate-400"> ({cqCount} generated)</span>
            )}
          </>
        ) : (
          <span data-testid="onboarding-cq-count">{cqCount}</span>
        )}
        {docCount === 0 && (
          <span className="ml-2 text-slate-500">
            — process sources first on the{" "}
            <Link href="/sources" className="underline">
              Sources
            </Link>{" "}
            screen.
          </span>
        )}
      </div>

      <CQGenerationPanel
        docCount={docCount}
        cqCount={cqCount}
        onGenerated={(n) => {
          setCqCount(n);
          void cqSummary.refetch();
        }}
        onMerged={() => {
          void cqSummary.refetch();
          void mergeLatest.refetch();
        }}
      />

      <OntologyProposalPanel docCount={docCount} cqReady={cqCount > 0} />
    </div>
  );
}
