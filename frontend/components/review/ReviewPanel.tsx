"use client";
import { SchemaElementList } from "./SchemaElementList";
import { CQCanvas, type CQCanvasItem } from "@/components/cq-canvas/CQCanvas";
import { useReviewElements } from "@/lib/query/review";
import { useReviewStore } from "@/lib/state/review-store";
import { ReviewEmptyState } from "./ReviewEmptyState";
import { ReviewErrorState } from "./ReviewErrorState";
import {
  RECONCILIATION_SIDEBAR_DIVERGENCE_MAP,
  RECONCILIATION_SIDEBAR_DOCUMENTED_REALITY,
  RECONCILIATION_SIDEBAR_GAP_REPORT,
  RECONCILIATION_SIDEBAR_TITLE,
} from "@/lib/recon/report_copy";

export type ReviewPanelProps = {
  sessionId: string;
  reconciliationAvailability?: {
    divergence_map: boolean;
    documented_reality: boolean;
    gap_report: boolean;
  };
};

export function ReviewPanel({
  sessionId,
  reconciliationAvailability,
}: ReviewPanelProps) {
  const { data: elements, isLoading, error } = useReviewElements(sessionId);

  if (error) return <ReviewErrorState error={error} />;
  if (isLoading) return <div data-testid="review-loading" className="p-4 text-sm text-slate-500">Loading review...</div>;
  if (!elements || elements.length === 0) return <ReviewEmptyState />;

  const reconAvail = reconciliationAvailability ?? {
    divergence_map: false,
    documented_reality: false,
    gap_report: false,
  };
  const showSidebar =
    reconAvail.divergence_map ||
    reconAvail.documented_reality ||
    reconAvail.gap_report;

  return (
    <div data-testid="review-panel" className="flex h-full gap-4 p-4">
      <div className="w-1/2 overflow-auto border-r border-border pr-4" data-testid="review-left-panel">
        <SchemaElementList sessionId={sessionId} elements={elements} />
      </div>
      <div className="w-1/2 overflow-auto" data-testid="review-right-panel">
        <CQCanvas items={[]} />
        {showSidebar ? (
          <nav
            data-testid="reconciliation-sidebar"
            className="mt-4 rounded border border-slate-200 p-3"
          >
            <h3 className="text-sm font-medium">
              {RECONCILIATION_SIDEBAR_TITLE}
            </h3>
            <ul className="mt-2 flex flex-col gap-1 text-sm">
              {reconAvail.divergence_map ? (
                <li data-testid="reconciliation-sidebar-divergence-map">
                  {RECONCILIATION_SIDEBAR_DIVERGENCE_MAP}
                </li>
              ) : null}
              {reconAvail.documented_reality ? (
                <li data-testid="reconciliation-sidebar-documented-reality">
                  {RECONCILIATION_SIDEBAR_DOCUMENTED_REALITY}
                </li>
              ) : null}
              {reconAvail.gap_report ? (
                <li data-testid="reconciliation-sidebar-gap-report">
                  {RECONCILIATION_SIDEBAR_GAP_REPORT}
                </li>
              ) : null}
            </ul>
          </nav>
        ) : null}
      </div>
    </div>
  );
}
