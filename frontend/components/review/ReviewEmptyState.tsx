"use client";
export function ReviewEmptyState() {
  return (
    <div data-testid="review-empty-state" className="flex items-center justify-center p-8 text-sm text-slate-500">
      No active review session. Start a review to see schema elements.
    </div>
  );
}
