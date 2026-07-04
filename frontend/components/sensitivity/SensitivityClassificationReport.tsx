"use client";

/**
 * SensitivityClassificationReport — tag inventory + coverage breakdown
 * + untagged-rule list (D344).
 *
 * D120/D217: renders `coverage_band` only — `coverage_score` is
 * server-side and never serialized.
 */

import type { SensitivityClassificationReportResponse } from "@/lib/api/types";
import { SENSITIVITY_COPY } from "@/lib/sensitivity/copy";
import { SensitivityCoverageBadge } from "./SensitivityCoverageBadge";

export type SensitivityClassificationReportProps = {
  report: SensitivityClassificationReportResponse;
};

export function SensitivityClassificationReport({
  report,
}: SensitivityClassificationReportProps) {
  return (
    <div
      data-testid="sensitivity-classification-report"
      className="flex flex-col gap-4"
    >
      <header className="flex items-center justify-between gap-2">
        <div>
          <p className="text-[10px] uppercase text-slate-500">Report</p>
          <p
            data-testid="sensitivity-report-id"
            className="font-mono text-[11px] text-slate-700"
          >
            {report.report_id}
          </p>
          <p className="text-[10px] text-slate-500">
            Generated {report.generated_at}
          </p>
        </div>
        <SensitivityCoverageBadge band={report.coverage_band} />
      </header>

      {report.corpus_below_floor ? (
        <p
          data-testid="sensitivity-below-floor-banner"
          className="rounded border border-slate-300 bg-slate-50 p-2 text-xs text-slate-700"
        >
          {SENSITIVITY_COPY.belowFloorBanner}
        </p>
      ) : null}

      <section
        data-testid="sensitivity-tag-inventory"
        className="rounded border border-slate-200 p-2"
      >
        <h3 className="mb-1 text-xs font-semibold text-slate-900">
          {SENSITIVITY_COPY.tagInventoryHeading}
        </h3>
        {report.tag_inventory.length === 0 ? (
          <p className="text-[11px] italic text-slate-500">
            {SENSITIVITY_COPY.tagInventoryEmpty}
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {report.tag_inventory.map((entry) => (
              <li
                key={entry.tag_name}
                data-testid={`sensitivity-tag-row-${entry.tag_name}`}
                className="flex items-center justify-between text-[11px]"
              >
                <span className="font-mono text-slate-900">
                  {entry.tag_name}
                </span>
                <span className="text-slate-600">
                  {entry.rule_count} rules · {entry.cluster_count} clusters
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section
        data-testid="sensitivity-coverage-breakdown"
        className="rounded border border-slate-200 p-2"
      >
        <h3 className="mb-1 text-xs font-semibold text-slate-900">
          {SENSITIVITY_COPY.coverageBreakdownHeading}
        </h3>
        {report.coverage_breakdown.length === 0 ? (
          <p className="text-[11px] italic text-slate-500">
            {SENSITIVITY_COPY.coverageBreakdownEmpty}
          </p>
        ) : (
          <table className="w-full table-auto border-collapse text-[11px]">
            <thead>
              <tr className="border-b border-slate-200 text-left text-slate-600">
                <th className="px-2 py-1">Resource kind</th>
                <th className="px-2 py-1">Action</th>
                <th className="px-2 py-1">Tagged / Total</th>
              </tr>
            </thead>
            <tbody>
              {report.coverage_breakdown.map((cell, idx) => (
                <tr
                  key={`${cell.resource_kind}-${cell.action}-${idx}`}
                  data-testid={`sensitivity-coverage-cell-${cell.resource_kind}-${cell.action}`}
                  className="border-b border-slate-100"
                >
                  <td className="px-2 py-1">{cell.resource_kind}</td>
                  <td className="px-2 py-1">{cell.action}</td>
                  <td className="px-2 py-1">
                    {cell.tagged_rule_count} / {cell.total_rule_count}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section
        data-testid="sensitivity-untagged-rules"
        className="rounded border border-slate-200 p-2"
      >
        <h3 className="mb-1 text-xs font-semibold text-slate-900">
          {SENSITIVITY_COPY.untaggedRulesHeading}
        </h3>
        {report.truncated ? (
          <p
            data-testid="sensitivity-untagged-truncated"
            className="mb-1 text-[10px] text-amber-700"
          >
            {SENSITIVITY_COPY.untaggedRulesTruncated}
          </p>
        ) : null}
        {report.untagged_rules.length === 0 ? (
          <p className="text-[11px] italic text-slate-500">
            {SENSITIVITY_COPY.untaggedRulesEmpty}
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {report.untagged_rules.map((row, idx) => (
              <li
                key={`${row.cluster_id}-${row.resource_label}-${row.action}-${idx}`}
                data-testid={`sensitivity-untagged-row-${idx}`}
                className="text-[11px] text-slate-700"
              >
                <span className="font-semibold">{row.cluster_display_name}</span>
                {" — "}
                <span className="font-mono">{row.resource_label}</span>
                {" · "}
                <span>{row.resource_kind}</span>
                {" · "}
                <span>{row.action}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section
        data-testid="sensitivity-hygiene-findings"
        className="rounded border border-slate-200 p-2"
      >
        <h3 className="mb-1 text-xs font-semibold text-slate-900">
          {SENSITIVITY_COPY.hygieneFindingsHeading}
        </h3>
        {report.tag_hygiene_findings.length === 0 ? (
          <p className="text-[11px] italic text-slate-500">
            {SENSITIVITY_COPY.hygieneFindingsEmpty}
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {report.tag_hygiene_findings.map((finding, idx) => (
              <li
                key={`${finding.tag_name}-${finding.similar_to}-${idx}`}
                data-testid={`sensitivity-hygiene-row-${idx}`}
                className="text-[11px] text-slate-700"
              >
                <span className="font-mono">{finding.tag_name}</span>
                {" ≈ "}
                <span className="font-mono">{finding.similar_to}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
