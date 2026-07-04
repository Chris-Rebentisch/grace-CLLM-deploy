"use client";

/**
 * SensitivityAuditTrailFilter — single-tag chip selector + result list.
 *
 * Issues a `GET /api/sensitivity/audit-trail` query for the entered
 * tag. The CP3-shipped backend route returns 200 with empty events;
 * the body lights up in CP5 (D346). The component handles both states
 * gracefully.
 */

import { useCallback, useState } from "react";
import { sensitivityApi } from "@/lib/api/sensitivity";
import { SENSITIVITY_COPY } from "@/lib/sensitivity/copy";
import type {
  SensitivityAuditTrailListResponse,
  SensitivityAuditTrailRow,
} from "@/lib/api/types";
import { AuditTrailRow } from "./AuditTrailRow";

export type SensitivityAuditTrailFilterProps = {
  matrixId?: string | null;
  onApplied?: (args: {
    tag: string;
    matrix_id: string | null;
    result_count: number;
  }) => void;
};

export function SensitivityAuditTrailFilter({
  matrixId = null,
  onApplied,
}: SensitivityAuditTrailFilterProps) {
  const [tag, setTag] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [results, setResults] =
    useState<SensitivityAuditTrailListResponse | null>(null);

  const onApply = useCallback(async () => {
    const trimmed = tag.trim();
    if (trimmed === "") {
      setErr("Tag is required");
      return;
    }
    setSubmitting(true);
    setErr(null);
    try {
      const out = await sensitivityApi.listAuditTrail({
        tag: trimmed,
        matrixId,
      });
      setResults(out);
      onApplied?.({
        tag: trimmed,
        matrix_id: matrixId ?? null,
        result_count: out.events.length,
      });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Audit-trail query failed");
    } finally {
      setSubmitting(false);
    }
  }, [tag, matrixId, onApplied]);

  return (
    <section
      data-testid="sensitivity-audit-trail-filter"
      className="flex flex-col gap-3 rounded-md border border-slate-200 bg-white p-3"
    >
      <div>
        <h2 className="text-sm font-semibold text-slate-900">
          {SENSITIVITY_COPY.auditTrailHeading}
        </h2>
        <p className="text-[11px] text-slate-600">
          {SENSITIVITY_COPY.auditTrailFilterPrompt}
        </p>
      </div>
      <p
        data-testid="sensitivity-audit-trail-runbook-hint"
        className="rounded border border-amber-300 bg-amber-50 p-2 text-[11px] text-amber-900"
      >
        {SENSITIVITY_COPY.auditTrailRunbookHint}
      </p>

      <label
        className="flex flex-col gap-1 text-[11px] text-slate-600"
        htmlFor="sensitivity-audit-trail-tag-input"
      >
        <span>{SENSITIVITY_COPY.auditTrailTagInputLabel}</span>
        <input
          id="sensitivity-audit-trail-tag-input"
          data-testid="sensitivity-audit-trail-tag-input"
          type="text"
          value={tag}
          onChange={(e) => setTag(e.target.value)}
          placeholder={SENSITIVITY_COPY.auditTrailTagInputPlaceholder}
          disabled={submitting}
          className="rounded border border-slate-300 bg-white px-2 py-1 text-xs text-slate-900 disabled:opacity-50"
        />
      </label>

      <div className="flex justify-end">
        <button
          type="button"
          data-testid="sensitivity-audit-trail-apply"
          onClick={onApply}
          disabled={submitting}
          className="rounded border border-slate-700 bg-slate-800 px-3 py-1 text-xs font-medium text-white disabled:opacity-50"
        >
          {submitting ? "Applying…" : SENSITIVITY_COPY.auditTrailApply}
        </button>
      </div>

      {err ? (
        <p
          data-testid="sensitivity-audit-trail-error"
          className="text-xs text-rose-700"
        >
          {err}
        </p>
      ) : null}

      {results ? (
        results.events.length === 0 ? (
          <p
            data-testid="sensitivity-audit-trail-empty"
            className="text-[11px] italic text-slate-500"
          >
            {SENSITIVITY_COPY.auditTrailEmpty}
          </p>
        ) : (
          <ul
            data-testid="sensitivity-audit-trail-list"
            className="flex flex-col gap-1"
          >
            {results.events.map((row: SensitivityAuditTrailRow) => (
              <AuditTrailRow key={row.query_event_id} row={row} />
            ))}
          </ul>
        )
      ) : null}
    </section>
  );
}
