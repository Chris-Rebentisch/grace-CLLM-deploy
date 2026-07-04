"use client";

export function StalledBanner() {
  return (
    <div
      data-testid="stalled-banner"
      className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950"
      role="status"
    >
      This directive appears stalled: recent velocity shows no upward trend against
      the configured floor. Consider executive review.
    </div>
  );
}
