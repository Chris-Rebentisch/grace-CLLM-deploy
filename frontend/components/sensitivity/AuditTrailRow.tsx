"use client";

/**
 * AuditTrailRow — single sensitivity audit-trail row presenter.
 *
 * Renders a query-event row matched by a sensitivity tag filter. The
 * back-end body for `/api/sensitivity/audit-trail` lights up in CP5
 * (D346 mandatory-context cypher rewriter); CP6 ships the presenter
 * so the surface compiles and renders today.
 */

import type { SensitivityAuditTrailRow } from "@/lib/api/types";

export type AuditTrailRowProps = {
  row: SensitivityAuditTrailRow;
};

export function AuditTrailRow({ row }: AuditTrailRowProps) {
  return (
    <li
      data-testid={`sensitivity-audit-trail-row-${row.query_event_id}`}
      className="rounded border border-slate-200 bg-white px-2 py-1.5 text-[11px] text-slate-800"
    >
      <div className="flex items-center justify-between gap-2">
        <span
          data-testid={`sensitivity-audit-trail-query-id-${row.query_event_id}`}
          className="font-mono text-[10px] text-slate-700"
        >
          {row.query_event_id}
        </span>
        <span className="text-[10px] text-slate-500">{row.occurred_at}</span>
      </div>
      <ul className="mt-1 flex flex-wrap gap-1">
        {row.sensitivity_tags.map((tag) => (
          <li
            key={tag}
            data-testid={`sensitivity-audit-trail-tag-${row.query_event_id}-${tag}`}
            className="rounded border border-slate-300 bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-700"
          >
            {tag}
          </li>
        ))}
      </ul>
    </li>
  );
}
