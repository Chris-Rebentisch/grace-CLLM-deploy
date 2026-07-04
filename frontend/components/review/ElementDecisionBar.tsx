"use client";
import { useDecide } from "@/lib/query/review";
import { useReviewStore } from "@/lib/state/review-store";
import type { ReviewDecisionType } from "@/lib/api/types";

const DECISION_TYPES: ReviewDecisionType[] = [
  "approved", "renamed", "edited", "split", "merged",
  "rejected", "redirected", "reclassified", "auto_approved",
];

const DESTRUCTIVE: Set<ReviewDecisionType> = new Set(["rejected", "merged", "split", "reclassified"]);

export type ElementDecisionBarProps = {
  sessionId: string;
  elementName: string;
  elementType: string;
  currentDecision: string | null;
};

export function ElementDecisionBar({ sessionId, elementName, elementType, currentDecision }: ElementDecisionBarProps) {
  const decideMutation = useDecide(sessionId);
  const { setHover } = useReviewStore();

  const handleDecide = (decision: ReviewDecisionType) => {
    decideMutation.mutate({
      element_type: elementType,
      element_name: elementName,
      decision,
    });
  };

  return (
    <div data-testid={`decision-bar-${elementName}`} className="flex flex-wrap gap-1">
      {DECISION_TYPES.map((d) => (
        <button
          key={d}
          type="button"
          data-testid={`decision-btn-${d}-${elementName}`}
          data-destructive={DESTRUCTIVE.has(d) ? "true" : "false"}
          onClick={() => handleDecide(d)}
          onMouseEnter={() => setHover(elementName, d)}
          onMouseLeave={() => setHover(null, null)}
          disabled={decideMutation.isPending}
          className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
            currentDecision === d
              ? "bg-slate-800 text-white"
              : DESTRUCTIVE.has(d)
                ? "bg-red-50 text-red-700 hover:bg-red-100"
                : "bg-slate-100 text-slate-700 hover:bg-slate-200"
          }`}
        >
          {d}
        </button>
      ))}
    </div>
  );
}
