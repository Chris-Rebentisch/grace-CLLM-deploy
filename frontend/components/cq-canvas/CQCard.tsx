"use client";

export type CoverageBand = "green" | "amber" | "red" | "gray";

export type CQCardProps = {
  cqId: string;
  cqText: string;
  cqType: string;
  domain: string;
  coverageBand: CoverageBand;
  isHighlighted?: boolean;
  onClick?: () => void;
};

const BAND_COLORS: Record<CoverageBand, string> = {
  green: "border-l-green-500 bg-green-50",
  amber: "border-l-amber-500 bg-amber-50",
  red: "border-l-red-500 bg-red-50",
  gray: "border-l-slate-400 bg-slate-50",
};

/**
 * Individual CQ card with coverage coloring.
 * No numeric coverage label per D217 -- visual color band only.
 */
export function CQCard({
  cqId,
  cqText,
  cqType,
  domain,
  coverageBand,
  isHighlighted = false,
  onClick,
}: CQCardProps) {
  return (
    <div
      data-testid={`cq-card-${cqId}`}
      data-coverage-band={coverageBand}
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onClick?.();
      }}
      className={`cursor-pointer rounded-md border-l-4 p-2 text-xs transition-shadow ${
        BAND_COLORS[coverageBand]
      } ${isHighlighted ? "ring-2 ring-blue-500" : ""}`}
    >
      <div className="mb-1 font-medium text-slate-700">{cqType}</div>
      <div className="text-slate-600">{cqText}</div>
      <div className="mt-1 text-[10px] text-slate-400">{domain}</div>
    </div>
  );
}
