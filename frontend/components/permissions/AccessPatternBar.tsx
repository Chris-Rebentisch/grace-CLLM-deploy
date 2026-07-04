"use client";

/**
 * AccessPatternBar — segment × layer × governance-artifact default-access row.
 *
 * Renders the per-cluster default access policy as a horizontal bar of
 * (segment, layer, artifact-class) cells. Operator decisions on each cell
 * collapse to one of three labels: `allow`, `deny`, `inherit`. The visual
 * encoding uses color bands only (D120/D217: no numeric scores).
 */

export type AccessDecision = "allow" | "deny" | "inherit";

export type AccessPatternCell = {
  segment_id: string;
  layer: string;
  artifact_class: string;
  decision: AccessDecision;
};

const DECISION_CLASSES: Record<AccessDecision, string> = {
  allow: "border-emerald-500 bg-emerald-50 text-emerald-900",
  deny: "border-rose-500 bg-rose-50 text-rose-900",
  inherit: "border-slate-300 bg-slate-50 text-slate-700",
};

export type AccessPatternBarProps = {
  clusterId: string;
  cells: AccessPatternCell[];
};

export function AccessPatternBar({ clusterId, cells }: AccessPatternBarProps) {
  return (
    <div
      data-testid={`access-pattern-bar-${clusterId}`}
      role="list"
      aria-label="Default access pattern"
      className="flex flex-wrap gap-1"
    >
      {cells.length === 0 ? (
        <span className="text-[11px] italic text-slate-500">
          No access cells defined
        </span>
      ) : (
        cells.map((cell) => {
          const key = `${cell.segment_id}-${cell.layer}-${cell.artifact_class}`;
          return (
            <span
              key={key}
              role="listitem"
              data-testid={`access-pattern-cell-${clusterId}-${key}`}
              title={`${cell.segment_id} / ${cell.layer} / ${cell.artifact_class}: ${cell.decision}`}
              className={`rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase ${DECISION_CLASSES[cell.decision]}`}
            >
              {cell.decision}
            </span>
          );
        })
      )}
    </div>
  );
}
