"use client";

/**
 * SampleCqPanel — D324 per-segment sample-CQ validation.
 *
 * Renders the (transient) sample CQs returned from
 * `POST /api/decomposition/runs/{run_id}/layer6/sample-cqs` for a single
 * segment. Each row exposes the `cq_type` label and an approve/reject
 * toggle. The accumulated approve/reject decisions are mirrored
 * upstream via {@link SampleCqPanelProps.onChange} so the parent page
 * can build the Layer 6 validation payload.
 */

import { useCallback, useState } from "react";

export type SampleCq = {
  question: string;
  cq_type: string;
  rationale?: string | null;
};

export type SampleCqDecision = {
  question: string;
  approved: boolean;
};

export type SampleCqPanelProps = {
  segmentName: string;
  cqs: SampleCq[];
  onChange?: (segmentName: string, decisions: SampleCqDecision[]) => void;
};

export function SampleCqPanel({
  segmentName,
  cqs,
  onChange,
}: SampleCqPanelProps) {
  // null = undecided, true = approved, false = rejected
  const [state, setState] = useState<Record<string, boolean | null>>(() => {
    const init: Record<string, boolean | null> = {};
    for (const c of cqs) init[c.question] = null;
    return init;
  });

  const fire = useCallback(
    (next: Record<string, boolean | null>) => {
      if (!onChange) return;
      const decisions: SampleCqDecision[] = cqs
        .map((c) => ({
          question: c.question,
          approved: next[c.question] === true,
        }))
        .filter((_, i) => state[cqs[i].question] !== null || true);
      onChange(segmentName, decisions);
    },
    [cqs, onChange, segmentName, state],
  );

  const setDecision = (question: string, approved: boolean) => {
    setState((prev) => {
      const next = { ...prev, [question]: approved };
      fire(next);
      return next;
    });
  };

  return (
    <section
      data-testid={`sample-cq-panel-${segmentName}`}
      className="rounded-md border border-slate-200 bg-white p-2 text-xs"
    >
      <header className="mb-2 flex items-center justify-between">
        <h4 className="font-medium text-slate-800">
          Sample CQs — segment <span className="font-mono">{segmentName}</span>
        </h4>
        <span className="text-slate-500">{cqs.length} candidate(s)</span>
      </header>
      {cqs.length === 0 ? (
        <p
          data-testid={`sample-cq-empty-${segmentName}`}
          className="italic text-slate-500"
        >
          No sample CQs returned for this segment.
        </p>
      ) : (
        <ul className="space-y-1">
          {cqs.map((cq) => {
            const decision = state[cq.question];
            return (
              <li
                key={cq.question}
                data-testid={`sample-cq-row-${segmentName}-${encodeURIComponent(cq.question).slice(0, 24)}`}
                className="flex items-start justify-between gap-2 rounded border border-slate-100 px-2 py-1"
              >
                <div className="flex-1">
                  <div className="text-slate-800">{cq.question}</div>
                  <div className="mt-0.5">
                    <span
                      data-testid={`sample-cq-type-${segmentName}`}
                      className="inline-block rounded bg-slate-100 px-1 text-[10px] uppercase tracking-wide text-slate-600"
                    >
                      {cq.cq_type}
                    </span>
                  </div>
                </div>
                <div className="flex shrink-0 gap-1">
                  <button
                    type="button"
                    data-testid={`sample-cq-approve-${segmentName}`}
                    aria-pressed={decision === true}
                    onClick={() => setDecision(cq.question, true)}
                    className={`rounded border px-2 py-0.5 ${
                      decision === true
                        ? "border-emerald-500 bg-emerald-50 text-emerald-900"
                        : "border-slate-300 bg-white text-slate-700"
                    }`}
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    data-testid={`sample-cq-reject-${segmentName}`}
                    aria-pressed={decision === false}
                    onClick={() => setDecision(cq.question, false)}
                    className={`rounded border px-2 py-0.5 ${
                      decision === false
                        ? "border-rose-500 bg-rose-50 text-rose-900"
                        : "border-slate-300 bg-white text-slate-700"
                    }`}
                  >
                    Reject
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
