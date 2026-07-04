"use client";
export function ReviewErrorState({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : "An unexpected error occurred";
  return (
    <div data-testid="review-error-state" className="flex items-center justify-center p-8 text-sm text-red-600">
      <div>
        <div className="font-medium">Review Error</div>
        <div className="mt-1 text-xs">{message}</div>
      </div>
    </div>
  );
}
