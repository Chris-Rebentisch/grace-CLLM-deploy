"use client";
import { useReviewProgress } from "@/lib/query/review";

export function ReviewProgressBar({ sessionId }: { sessionId: string }) {
  const { data } = useReviewProgress(sessionId);
  const total = (data?.total_elements as number) ?? 0;
  const reviewed = (data?.reviewed_elements as number) ?? 0;
  const pct = total > 0 ? Math.round((reviewed / total) * 100) : 0;

  return (
    <div data-testid="review-progress-bar" className="mb-4">
      <div className="flex items-center justify-between text-xs text-slate-600">
        <span>{reviewed} / {total} reviewed</span>
        <span>{pct}%</span>
      </div>
      <div className="mt-1 h-2 rounded-full bg-slate-200">
        <div className="h-full rounded-full bg-slate-800 transition-all" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
