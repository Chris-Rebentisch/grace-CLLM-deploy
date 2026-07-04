"use client";

import { useGraphStore } from "@/lib/state/graph-store";

export type PaginationControlsProps = {
  nextCursor: string | null;
  onPageChange?: (cursor: string | null) => void;
  // When backend returns filter_mismatch (400), caller passes this to reset.
  mismatch?: boolean;
};

export function PaginationControls(props: PaginationControlsProps) {
  const cursor = useGraphStore((s) => s.paginationCursor);
  const setCursor = useGraphStore((s) => s.setCursor);
  const resetCursor = useGraphStore((s) => s.resetCursor);

  // Filter mismatch auto-resets the cursor the next render after detection.
  if (props.mismatch) {
    // Fire-and-forget reset; no render lock needed since this is idempotent.
    setTimeout(resetCursor, 0);
  }

  const goNext = () => {
    setCursor(props.nextCursor);
    props.onPageChange?.(props.nextCursor);
  };
  const goFirst = () => {
    resetCursor();
    props.onPageChange?.(null);
  };

  const onFirstPage = cursor === null;

  return (
    <div
      data-testid="pagination-controls"
      className="flex items-center gap-2 px-3 py-2 border-t bg-white text-xs"
    >
      <button
        type="button"
        data-testid="pagination-first"
        disabled={onFirstPage}
        onClick={goFirst}
        className="rounded-md border border-slate-300 bg-white px-2 py-1 text-slate-700 disabled:opacity-50"
      >
        First page
      </button>
      <button
        type="button"
        data-testid="pagination-next"
        disabled={!props.nextCursor}
        onClick={goNext}
        className="rounded-md border border-slate-300 bg-white px-2 py-1 text-slate-700 disabled:opacity-50"
      >
        Next page
      </button>
      <span className="text-slate-500" data-testid="pagination-state">
        {onFirstPage ? "Page 1" : "Next page ready"}
      </span>
      {!props.nextCursor && !onFirstPage && (
        <span className="text-slate-500" data-testid="pagination-end">
          End of results.
        </span>
      )}
    </div>
  );
}
