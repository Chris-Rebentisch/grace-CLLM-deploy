"use client";

export function ClaimEmptyState() {
  return (
    <div
      data-testid="claim-empty-state"
      className="flex h-full flex-col items-center justify-center p-6 text-sm text-slate-500"
    >
      No claims match the current filters.
    </div>
  );
}
