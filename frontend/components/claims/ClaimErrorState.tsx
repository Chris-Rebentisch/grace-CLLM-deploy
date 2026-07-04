"use client";

export function ClaimErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div
      data-testid="claim-error-state"
      className="flex h-full flex-col items-center justify-center gap-2 p-6 text-sm text-rose-700"
    >
      Failed to load claims.
      <button
        type="button"
        onClick={onRetry}
        className="rounded border px-2 py-1 text-xs"
        data-testid="claim-error-retry"
      >
        Retry
      </button>
    </div>
  );
}
